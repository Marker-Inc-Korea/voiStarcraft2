const test = require("node:test");
const assert = require("node:assert/strict");
const P = require("../sketch/payload.js");

const STROKES = [[[0.1, 0.9, 0], [0.5, 0.5, 300], [0.9, 0.1, 600]]];
const RECT = { x: 10.4, y: 700.2, w: 240, h: 240 };

test("payload matches the HTTP contract exactly and only that", () => {
  const body = P.buildPayload({ updateId: "sk-1", strokes: STROKES, imagePngB64: "QUJD", transcript: "저기로 가", rect: RECT });
  assert.deepEqual(Object.keys(body).sort(), ["image_png_b64", "minimap_rect", "strokes", "transcript", "update_id"]);
  assert.equal(body.update_id, "sk-1"); assert.equal(body.image_png_b64, "QUJD"); assert.equal(body.transcript, "저기로 가");
  assert.deepEqual(body.strokes, STROKES);
  assert.deepEqual(body.minimap_rect, { x: 10, y: 700, w: 240, h: 240 });
});
test("no screen-capture permission -> image_png_b64 is null, never an empty string; a data: prefix is stripped", () => {
  assert.equal(P.buildPayload({ updateId: "a", strokes: STROKES, imagePngB64: null, transcript: "", rect: RECT }).image_png_b64, null);
  assert.equal(P.buildPayload({ updateId: "a", strokes: STROKES, imagePngB64: "", transcript: "", rect: RECT }).image_png_b64, null);
  assert.equal(P.buildPayload({ updateId: "a", strokes: STROKES, imagePngB64: "data:image/png;base64,QUJD", transcript: "", rect: RECT }).image_png_b64, "QUJD");
});
test("strokes are re-validated: out-of-frame, non-finite, decreasing time and empty strokes are rejected, never repaired", () => {
  const bad = [
    [[[1.2, 0.5, 0], [0.5, 0.5, 10]]],
    [[[0.2, NaN, 0], [0.5, 0.5, 10]]],
    [[[0.2, 0.2, 100], [0.5, 0.5, 10]]],
    [[[0.2, 0.2, 0]]],
    [],
    [[]],
    "x",
  ];
  for (const strokes of bad) assert.throws(() => P.buildPayload({ updateId: "a", strokes, imagePngB64: null, transcript: "", rect: RECT }), /stroke/, JSON.stringify(strokes));
});
test("coordinates are rounded to 4 decimals and timestamps to integers", () => {
  const body = P.buildPayload({ updateId: "a", strokes: [[[0.123456, 0.987654, 0.4], [0.5, 0.5, 10.6]]], imagePngB64: null, transcript: "", rect: RECT });
  assert.deepEqual(body.strokes, [[[0.1235, 0.9877, 0], [0.5, 0.5, 11]]]);
});
test("transcript is trimmed and capped; absent means empty string", () => {
  assert.equal(P.buildPayload({ updateId: "a", strokes: STROKES, imagePngB64: null, rect: RECT }).transcript, "");
  assert.equal(P.buildPayload({ updateId: "a", strokes: STROKES, imagePngB64: null, transcript: `  ${"가".repeat(500)}  `, rect: RECT }).transcript.length, P.MAX_TRANSCRIPT);
});
test("an oversized image is refused (the crop is meant to be <= 512 px)", () => {
  assert.throws(() => P.buildPayload({ updateId: "a", strokes: STROKES, imagePngB64: "A".repeat(P.MAX_IMAGE_B64 + 1), transcript: "", rect: RECT }), /image/);
});
test("update ids are unique and shaped", () => {
  const ids = new Set(Array.from({ length: 50 }, () => P.newUpdateId()));
  assert.equal(ids.size, 50);
  for (const id of ids) assert.match(id, /^sk-[a-z0-9]+-[a-z0-9]+$/);
});
test("transcript window: only what was said in the last 10 s, de-duplicated per turn, oldest first", () => {
  const w = P.createTranscriptWindow({ windowMs: 10000 });
  w.push(1, "앞마당으로", 1000, false); w.push(1, "앞마당으로 가서", 2000, true);
  w.push(2, "저기 공격", 9000, true);
  w.push(0, "아주 옛날", -20000, true);
  assert.equal(w.text(11000), "앞마당으로 가서 저기 공격");
  assert.equal(w.text(13000), "저기 공격", "turn 1 is older than 10 s");
  assert.equal(w.text(30000), "");
});
test("response guard: execute needs a plan with real coordinates; anything else is shown as text only", () => {
  const route = { waypoints: [{ nx: 0.2, ny: 0.8, x: 20, y: 30, label_ko: "앞마당" }], final: { action: "attack", nx: 0.9, ny: 0.1, x: 90, y: 10, label_ko: "적 본진" } };
  const ok = P.interpretResponse({ kind: "execute", plan_id: "p1", narration_ko: "공격합니다", question_ko: null, route, squad: { label_ko: "해병", unit_count: 8 } });
  assert.equal(ok.kind, "execute"); assert.equal(ok.text, "공격합니다"); assert.equal(ok.planId, "p1");
  const noRoute = P.interpretResponse({ kind: "execute", plan_id: "p1", narration_ko: "공격합니다", route: null });
  assert.equal(noRoute.kind, "invalid", "execute without a route is never reported as executing");
  assert.match(noRoute.text, /경로/);
  const badNx = P.interpretResponse({ kind: "execute", plan_id: "p1", narration_ko: "x", route: { ...route, final: { ...route.final, nx: 1.5 } } });
  assert.equal(badNx.kind, "invalid");
  const ask = P.interpretResponse({ kind: "ask", plan_id: "p2", narration_ko: "", question_ko: "어느 쪽으로 보낼까요?", route: null });
  assert.equal(ask.kind, "ask"); assert.equal(ask.text, "어느 쪽으로 보낼까요?");
  for (const kind of ["refuse", "none"]) assert.equal(P.interpretResponse({ kind, plan_id: "p", narration_ko: "알아볼 수 없습니다", route: null }).text, "알아볼 수 없습니다");
  assert.equal(P.interpretResponse({ kind: "none", narration_ko: "", route: null }).text.length > 0, true, "a Korean fallback exists when the narration is empty");
  assert.equal(P.interpretResponse({ kind: "weird" }).kind, "invalid");
  assert.equal(P.interpretResponse(null).kind, "invalid");
});
