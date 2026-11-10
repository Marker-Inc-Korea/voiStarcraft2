"""route_run: decision flattening, concurrent replicate runner and result file shape, with an injected fake interpret().  No network."""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import route_corpus as RC  # noqa: E402
import route_run as RR  # noqa: E402
import route_scoring as RS  # noqa: E402


def _oracle(row):
    g = row["gold"]
    if g["kind"] != "execute":
        return {"kind": g["kind"], "plan_id": "p", "narration_ko": "", "question_ko": "?" if g["kind"] == "ask" else None, "route": None, "squad": None}
    return {"kind": "execute", "plan_id": "p", "narration_ko": "", "question_ko": None,
            "route": {"waypoints": [{"x": w[0], "y": w[1], "nx": 0, "ny": 0, "label_ko": ""} for w in g["waypoints"]],
                      "final": {"action": g["final"]["action"], "x": g["final"]["x"], "y": g["final"]["y"], "nx": 0, "ny": 0, "label_ko": ""}},
            "squad": {"label_ko": (g["squad"]["type_ko"] or "") + " 부대", "unit_count": g["squad"]["count"]}}


def test_flatten_accepts_dicts_and_objects_and_missing_route():
    d = _oracle({"gold": {"kind": "execute", "waypoints": [[1.0, 2.0]], "final": {"action": "attack", "x": 3.0, "y": 4.0}, "squad": {"type_ko": "마린", "count": 5}}})
    flat = RR.flatten_decision(d)
    assert flat["kind"] == "execute" and flat["waypoints"] == [[1.0, 2.0]] and flat["final"] == {"action": "attack", "x": 3.0, "y": 4.0}
    assert flat["squad"] == {"unit_count": 5, "label_ko": "마린 부대"}
    obj = SimpleNamespace(kind="ask", plan_id="x", narration_ko="", question_ko="어디?", route=None, squad=None)
    assert RR.flatten_decision(obj) == {"kind": "ask", "waypoints": [], "final": None, "squad": None, "question_ko": "어디?"}


def test_run_variant_with_oracle_is_all_correct_and_unsafe_free():
    rows = RC.load_rows()[:: 7]
    lock = threading.Lock()
    active = {"now": 0, "max": 0}
    rowmap = {r["request"]["update_id"]: r for r in rows}

    def interpret(req, world, vlm):
        with lock:
            active["now"] += 1
            active["max"] = max(active["max"], active["now"])
        try:
            return _oracle(rowmap[req["update_id"]])
        finally:
            with lock:
                active["now"] -= 1

    run = RR.run_variant("oracle", rows, interpret, make_vlm=lambda: None, reps=2, concurrency=4, render_images=False)
    assert len(run["reps"]) == 2 and all(len(r["predictions"]) == len(rows) for r in run["reps"])
    agg = run["aggregate"]["all"]
    assert agg["n"] == 2 * len(rows) and agg["unsafe"] == 0 and agg["unanswered"] == 0
    assert agg["kind_acc"] == 1.0 and agg["action_ok"] == 1.0
    assert 1 <= active["max"] <= 4
    assert run["meta"]["reps"] == 2 and len(run["meta"]["loadavg_1_5_15"]) == 3 and "uptime" in run["meta"]


def test_exception_in_interpret_is_unanswered_not_a_crash_and_never_unsafe():
    rows = RC.load_rows()[:6]

    def boom(req, world, vlm):
        raise RuntimeError("proxy down sk-secret")

    run = RR.run_variant("boom", rows, boom, make_vlm=lambda: None, reps=1, concurrency=2, render_images=False)
    agg = run["aggregate"]["all"]
    assert agg["unanswered"] == 6 and agg["unsafe"] == 0
    assert "sk-secret" not in json.dumps(run)


def test_request_and_world_adapters_follow_the_shared_contract():
    row = RC.load_rows()[0]
    req = RR.build_request_body(row, render_images=False)
    assert set(req) >= {"update_id", "strokes", "image_png_b64", "transcript", "minimap_rect"} and req["image_png_b64"] is None
    w = RR.build_world_dict(row)
    assert set(w) >= {"playable", "map_size", "own_units", "enemies", "places"} and callable(w["pathable"])
    assert len(w["own_units"]) >= 1 and all(len(u) == 4 for u in w["own_units"])


def test_models_list_is_the_assignment_set():
    assert RR.MODELS == ["gemini-3-flash", "gemini-3.8-flash-high", "claude-haiku-5-5", "claude-sonnet-4-6", "gpt-6-sol"]
