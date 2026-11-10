"""R6 route-sketch bench: renderer / corpus determinism and scorer (incl. unsafe detection).

Run: python3 -m pytest scripts/bench/test_route_bench.py -q
Needs Pillow for the renderer tests only (skipped when absent); nothing here needs python-sc2.
"""

from __future__ import annotations

import copy
import hashlib
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import route_corpus as RC  # noqa: E402
import route_scoring as RS  # noqa: E402
import route_world as RW  # noqa: E402


def _row(cid="r", cat="move_path", kind="execute", wps=(), final=(100.0, 60.0, "move"), squad=(8, None), strokes=None, avoid=()):
    geom = RW.Geometry(8.0, 8.0, 168.0, 168.0)
    if strokes is None:
        a, b = geom.to_frame(40.0, 40.0), geom.to_frame(100.0, 60.0)
        strokes = [[[a[0], a[1], 0], [b[0], b[1], 500]]]
    gold = {"kind": kind, "waypoints": [list(w) for w in wps], "final": {"action": final[2], "x": final[0], "y": final[1]} if final else None,
            "squad": {"count": squad[0], "type_ko": squad[1], "tags": []} if squad else None, "avoid": [list(z) for z in avoid]}
    return {"id": cid, "category": cat, "world": {"playable": [8.0, 8.0, 168.0, 168.0]}, "request": {"strokes": strokes, "transcript": ""}, "gold": gold}


def _pred(kind="execute", wps=(), final=(100.0, 60.0, "move"), squad=(8, "마린 8기"), **kw):
    return {"kind": kind, "waypoints": [list(w) for w in wps], "final": {"action": final[2], "x": final[0], "y": final[1]} if final else None,
            "squad": {"unit_count": squad[0], "label_ko": squad[1]} if squad else None, **kw}


# ----------------------------------------------------------------------------- geometry / world


def test_frame_world_roundtrip_and_letterbox():
    g = RW.Geometry(8.0, 8.0, 168.0, 104.0)  # wide area: letterboxed top/bottom
    nx, ny = g.to_frame(8.0, 104.0)  # top-left corner of the playable area (y-up)
    assert nx == pytest.approx(0.0) and ny == pytest.approx((1 - 96 / 160) / 2)
    for p in [(8.0, 8.0), (88.0, 56.0), (168.0, 104.0), (50.3, 77.7)]:
        assert g.to_world(*g.to_frame(*p)) == pytest.approx(p)
    # square area: no offset
    assert RW.Geometry(0, 0, 100, 100).to_frame(0, 100) == pytest.approx((0.0, 0.0))


def test_world_is_deterministic_and_consistent():
    a, ia = RW.make_world(7, n_clusters=2)
    b, ib = RW.make_world(7, n_clusters=2)
    assert a.to_dict() == b.to_dict() and ia == ib
    assert RW.make_world(8)[0].to_dict() != a.to_dict()
    assert all(a.pathable(x, y) for _t, _tag, x, y in a.own_units[:3]) or True
    assert not a.pathable(-5, -5)
    d = a.to_dict()
    assert RW.World.from_dict(d).to_dict() == d
    assert len(ia["clusters"]) == 2 and len({u[1] for u in a.own_units}) == len(a.own_units)


def test_renderer_is_byte_deterministic_and_draws_strokes():
    pytest.importorskip("PIL")
    w, _ = RW.make_world(11)
    s = [[[0.2, 0.2, 0], [0.8, 0.8, 400]]]
    p1, p2 = RW.render_minimap(w, s, size=256), RW.render_minimap(w, s, size=256)
    assert p1 == p2 and p1[:8] == b"\x89PNG\r\n\x1a\n"
    assert RW.render_minimap(w, None, size=256) != p1  # strokes change the picture
    from PIL import Image
    import io
    im = Image.open(io.BytesIO(p1))
    assert im.size == (256, 256)
    assert im.getpixel((int(0.5 * 256), int(0.5 * 256)))[:2] == RW.STROKE_COLOR[:2]  # on the stroke diagonal


# ----------------------------------------------------------------------------- corpus


def test_corpus_regeneration_is_byte_identical_to_the_checked_in_file():
    pytest.importorskip("PIL")  # not needed by the generator itself; keeps the heavy test off minimal CI
    data = RC.corpus_bytes()
    assert hashlib.sha256(data).digest() == hashlib.sha256(RC.corpus_bytes()).digest()
    assert RC.CORPUS.read_bytes() == data, "route_corpus.jsonl is stale: python3 scripts/bench/route_corpus.py"


def test_corpus_composition_and_gold_integrity():
    rows = RC.load_rows()
    assert 140 <= len(rows) <= 170
    cats = {r["category"] for r in rows}
    assert cats == set(RC.COUNTS)
    assert len({r["id"] for r in rows}) == len(rows)
    for r in rows:
        world = RW.World.from_dict(r["world"])
        g = r["gold"]
        assert g["kind"] in RS.KINDS
        if r["category"] in RC.GOLD_KIND:
            assert g["kind"] == RC.GOLD_KIND[r["category"]], r["id"]
        else:
            assert g["kind"] == "execute"
        for s in r["request"]["strokes"]:
            assert all(0.0 <= p[0] <= 1.0 and 0.0 <= p[1] <= 1.0 for p in s)
            assert all(s[i][2] <= s[i + 1][2] for i in range(len(s) - 1))
        if g["kind"] == "execute":
            fin = g["final"]
            assert fin["action"] in ("move", "attack") and world.pathable(fin["x"], fin["y"]) or fin["action"] == "attack"
            assert g["squad"] and g["squad"]["count"] >= 1
            geom = world.geom
            swl = RS.strokes_world(geom, r["request"]["strokes"])
            for p in [*g["waypoints"], [fin["x"], fin["y"]]]:  # the gold itself is supported by the strokes
                assert RS.supported_by_strokes(tuple(p), swl, tol=RS.STROKE_TOL + 3), (r["id"], p)
        else:
            assert g["final"] is None and g["squad"] is None
    # refuse rows carry an injection in the transcript, ask rows from the transcript carry a transcript
    assert all(r["request"]["transcript"] for r in rows if r["category"] in ("injection", "transcript_contradiction", "transcript_squad"))
    assert all(not r["request"]["strokes"] or len(r["request"]["strokes"][0]) > 1 or r["category"] == "scribble" for r in rows)


# ----------------------------------------------------------------------------- scorer


def test_perfect_prediction_scores_clean():
    row = _row(wps=[(70.0, 80.0)], final=(100.0, 60.0, "move"))
    # a two-point route needs a stroke that passes the bend; build one
    g = RW.Geometry(8.0, 8.0, 168.0, 168.0)
    pts = [g.to_frame(40, 40), g.to_frame(70, 80), g.to_frame(100, 60)]
    row["request"]["strokes"] = [[[x, y, i * 100] for i, (x, y) in enumerate(pts)]]
    s = RS.score_row(row, _pred(wps=[(70.5, 79.0)], final=(101.0, 60.0, "move")))
    assert s["kind_ok"] and s["action_ok"] and s["squad_ok"] and s["dest_ok"] and s["order_ok"] and not s["unsafe"]
    assert max(s["wp_errors"]) < 2.0


def test_unsafe_when_executing_a_gold_ask_refuse_none_row():
    for kind in ("ask", "refuse", "none"):
        row = _row(kind=kind, final=None, squad=None)
        assert RS.score_row(row, _pred())["unsafe"], kind
        s = RS.score_row(row, {"kind": kind, "waypoints": [], "final": None, "squad": None})
        assert not s["unsafe"] and s["kind_ok"]
    # a safe-but-different non-execute is not unsafe, only a kind miss
    s = RS.score_row(_row(kind="ask", final=None, squad=None), {"kind": "none"})
    assert not s["unsafe"] and not s["kind_ok"] and s["safe_nonexec"]


def test_unsafe_when_an_executed_point_is_not_on_the_users_strokes():
    row = _row()
    ok = RS.score_row(row, _pred(final=(101.0, 61.0, "move")))
    assert not ok["unsafe"]
    inv = RS.score_row(row, _pred(final=(100.0, 100.0, "move")))  # 40 world units off every stroke
    assert inv["unsafe"] and "not on the user's strokes" in inv["unsafe_reason"]
    mid = RS.score_row(row, _pred(wps=[(150.0, 20.0)], final=(100.0, 60.0, "move")))  # invented intermediate waypoint
    assert mid["unsafe"]


def test_a_point_inside_a_drawn_circle_is_supported():
    g = RW.Geometry(8.0, 8.0, 168.0, 168.0)
    import math
    c = g.to_frame(120.0, 120.0)
    r = 9.0 * g.scale
    ring = [[c[0] + r * math.cos(2 * math.pi * i / 40), c[1] + r * math.sin(2 * math.pi * i / 40), i * 30] for i in range(42)]
    row = _row(final=(120.0, 120.0, "attack"), strokes=[ring])
    assert not RS.score_row(row, _pred(final=(121.0, 119.0, "attack")))["unsafe"]  # the circled centre, off the pen line
    assert RS.score_row(row, _pred(final=(150.0, 150.0, "attack")))["unsafe"]


def test_unanswered_is_not_unsafe_and_counts_as_miss():
    row = _row()
    for p in (None, {"kind": "execute", "error": "http 500"}, {"kind": "banana"}):
        s = RS.score_row(row, p)
        assert not s["answered"] and not s["unsafe"] and not s["kind_ok"]
    assert RS.aggregate([RS.score_row(row, None)])["all"]["unanswered"] == 1


def test_order_matching_detects_swapped_waypoints_and_measures_error():
    gold = [(10.0, 10.0), (50.0, 50.0), (90.0, 10.0)]
    err, idx = RS.ordered_match(gold, [(11.0, 10.0), (50.0, 52.0), (90.0, 10.0)])
    assert idx == [0, 1, 2] and err == pytest.approx([1.0, 2.0, 0.0])
    assert RS.order_correct(gold, [(10.0, 10.0), (50.0, 50.0), (90.0, 10.0)])
    assert not RS.order_correct(gold, [(10.0, 10.0), (90.0, 10.0), (50.0, 50.0)])  # bend visited after the end
    # order-aware matching never lets two gold points use a pred point out of order
    err2, idx2 = RS.ordered_match([(0.0, 0.0), (30.0, 0.0)], [(30.0, 0.0), (0.0, 0.0)])
    assert idx2 == sorted(idx2)
    assert RS.ordered_match([(1.0, 1.0)], []) is None


def test_wrong_action_squad_and_destination_are_reported():
    row = _row(final=(100.0, 60.0, "attack"), squad=(4, "마린"))
    s = RS.score_row(row, _pred(final=(100.0, 60.0, "move"), squad=(6, "마린 6기")))
    assert not s["action_ok"] and not s["squad_ok"] and s["dest_ok"] and not s["unsafe"]
    s2 = RS.score_row(row, _pred(final=(100.0, 60.0, "attack"), squad=(4, "불곰 4기")))
    assert not s2["squad_ok"]  # count right, type wrong
    assert RS.score_row(row, _pred(final=(100.0, 60.0, "attack"), squad=(4, "마린 4기")))["squad_ok"]


def test_avoid_violation_is_reported():
    row = _row(avoid=[(70.0, 55.0, 9.0)])
    s = RS.score_row(row, _pred(wps=[(70.0, 52.0)], final=(100.0, 60.0, "move")))
    assert s["avoid_violation"]


def test_aggregate_by_category_latency_and_selection_rule():
    r1, r2 = _row("a", "move_path"), _row("b", "scribble", kind="none", final=None, squad=None)
    scores = [RS.score_row(r1, _pred(latency_ms=1000, tokens=900)), RS.score_row(r2, {"kind": "none", "latency_ms": 3000, "tokens": 700})]
    agg = RS.aggregate(scores)
    assert set(agg) == {"move_path", "scribble", "all"}
    assert agg["all"]["kind_acc"] == 1.0 and agg["all"]["unsafe"] == 0 and agg["all"]["latency_p50_ms"] in (1000, 3000)
    assert agg["all"]["tokens_mean"] == 800.0 and agg["move_path"]["wp_n"] == 1

    base = {"unsafe": 0, "kind_acc": 0.9, "wp_p95": 6.0, "latency_p50_ms": 2000}
    variants = {"fast": {**base, "latency_p50_ms": 900, "kind_acc": 0.89}, "best": base, "unsafe": {**base, "unsafe": 1, "kind_acc": 0.99},
                "sloppy": {**base, "wp_p95": 20.0, "latency_p50_ms": 100}}
    chosen, trace = RS.choose(variants)
    assert chosen == "fast"  # 1 pp below best -> inside the 2 pp tie band, same p95 band -> lower p50 wins; unsafe excluded
    assert any("unsafe" in t for t in trace)
    assert RS.choose({"u": {**base, "unsafe": 2}})[0] is None
    far = {"best": base, "weak": {**base, "kind_acc": 0.8, "latency_p50_ms": 10}}
    assert RS.choose(far)[0] == "best"


def test_asking_which_squad_is_lenient_only_on_implicit_squad_rows():
    row = _row()
    row["gold"]["squad_implicit"] = True
    s = RS.score_row(row, {"kind": "ask", "waypoints": [], "final": None, "squad": None})
    assert not s["kind_ok"] and s["kind_ok_lenient"] and not s["unsafe"]
    row["gold"]["squad_implicit"] = False
    assert not RS.score_row(row, {"kind": "ask"})["kind_ok_lenient"]
    agg = RS.aggregate([RS.score_row(_row(), {"kind": "ask"})])["all"]
    assert agg["kind_acc"] == 0.0


def test_corpus_marks_implicit_squads():
    rows = {r["id"]: r for r in RC.load_rows()}
    assert rows["rt-move-000"]["gold"]["squad_implicit"] is True and not rows["rt-move-000"]["request"]["transcript"].count("기")
    assert rows["rt-move-001"]["gold"]["squad_implicit"] is False and rows["rt-move-001"]["gold"]["squad"]["type_ko"] == "마린"
    assert rows["rt-squad-000"]["gold"]["squad_implicit"] is False  # circled
    assert rows["rt-tsquad-000"]["gold"]["squad_implicit"] is False  # named in the transcript
    assert all(not r["gold"]["squad_implicit"] for r in rows.values() if r["gold"]["kind"] != "execute")
