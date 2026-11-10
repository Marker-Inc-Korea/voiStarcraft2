"use strict";
// Sketch window runtime (renderer side). All state arrives from main over window.sketchApi; the only things sent back are the strokes the
// user drew with their own mouse, a cancel, and a calibration result. The ghost "agent cursor" is a DOM element positioned by CSS transform:
// nothing here can move the system cursor.
(function () {
  const api = window.sketchApi;
  const G = window.VoiSketchGeometry, S = window.VoiSketchStrokes, M = window.VoiRouteModel, V = window.VoiSketchView;
  const $ = id => document.getElementById(id);
  const body = document.body;
  const svg = $("svg"), frame = $("frame"), ghost = $("cursor"), ghostLabel = $("cursor-label");
  const SVG_NS = "http://www.w3.org/2000/svg";

  let layout = { ox: 0, oy: 0, w: 100, h: 100, width: 100, height: 100 };
  let mode = "idle";
  const model = M.createRouteModel();
  let recorder = S.createRecorder();
  let pointerId = null;
  let raf = 0;
  let lastCursorSeq = 0;

  function applyLayout(next) {
    layout = { ox: 0, oy: 0, ...next };
    for (const [k, v] of [["left", layout.ox], ["top", layout.oy], ["width", layout.w], ["height", layout.h]]) frame.style[k] = `${v}px`;
    svg.setAttribute("width", String(layout.width)); svg.setAttribute("height", String(layout.height));
    svg.setAttribute("viewBox", `0 0 ${layout.width} ${layout.height}`);
    svg.style.width = `${layout.width}px`; svg.style.height = `${layout.height}px`;
    if (layout.mode === "calibrate") setupCalibration();
    schedule();
  }

  // ---------- drawing (the user's own mouse; the window only accepts events while main says "draw") ----------
  const norm = event => { const n = G.normalize({ x: layout.ox, y: layout.oy, w: layout.w, h: layout.h }, event.clientX, event.clientY); return n; };
  body.addEventListener("pointerdown", event => {
    if (mode !== "draw" || event.button !== 0) return;
    const n = norm(event);
    if (!recorder.begin(n.nx, n.ny, event.timeStamp)) return;
    pointerId = event.pointerId;
    try { body.setPointerCapture(pointerId); } catch { /* capture is a nicety */ }
    schedule();
  });
  body.addEventListener("pointermove", event => {
    if (mode !== "draw" || event.pointerId !== pointerId) return;
    const batch = typeof event.getCoalescedEvents === "function" ? event.getCoalescedEvents() : [];
    for (const e of batch.length ? batch : [event]) { const n = norm(e); recorder.move(n.nx, n.ny, e.timeStamp); }
    schedule();
  });
  function endStroke(event) {
    if (mode !== "draw" || event.pointerId !== pointerId) return;
    pointerId = null;
    const n = norm(event);
    const result = recorder.end(n.nx, n.ny, event.timeStamp);
    if (result.kind === "tap") { recorder.cancel(); api.cancel("tap"); } // a click without a drag cancels the sketch
    schedule();
  }
  body.addEventListener("pointerup", endStroke);
  body.addEventListener("pointercancel", event => { if (event.pointerId === pointerId) { pointerId = null; recorder.finish(event.timeStamp); schedule(); } });
  window.addEventListener("keydown", event => { if (event.key === "Escape" && mode === "draw") api.cancel("esc"); });

  // ---------- main -> window ----------
  api.onSkin(skin => { body.dataset.skin = skin && skin.skin === "terminal" ? "terminal" : "cosmic"; body.dataset.motion = skin && skin.reduceMotion ? "off" : "on"; });
  api.onLayout(applyLayout);
  api.onMode(next => {
    mode = next && next.mode || "idle";
    body.dataset.mode = mode;
    $("calib").hidden = mode !== "calibrate";
    if (mode !== "draw") pointerId = null;
    schedule();
  });
  api.onCollect(() => { if (recorder.open()) recorder.finish(performance.now()); api.strokes(recorder.strokes()); });
  api.onClear(() => { recorder.cancel(); pointerId = null; model.clearSketch(); model.clearRoute(); lastCursorSeq = 0; ghost.hidden = true; schedule(); });
  api.onSubmitted(payload => { if (payload && Array.isArray(payload.strokes)) model.setSketch(String(payload.planId || ""), payload.strokes); schedule(); });
  api.onSettle(payload => { model.settleSketch(payload && payload.kind); schedule(); });
  api.onRouteEvent(event => { model.ingest(event); schedule(); });
  api.onRouteReset(() => { model.resetSeq(); });

  // ---------- render ----------
  function schedule() { if (!raf) raf = requestAnimationFrame(render); }
  function render() {
    raf = 0;
    const now = Date.now();
    const view = model.view(now);
    const live = recorder.preview();
    const scene = V.buildScene({ view, layout, live });
    svg.replaceChildren(V.materialize(document, scene));
    const c = V.cursorPlacement(view.cursor, layout);
    if (!c) ghost.hidden = true;
    else {
      const first = ghost.hidden;
      ghost.hidden = false;
      ghost.dataset.action = c.action;
      ghostLabel.textContent = c.label;
      if (first || c.seq !== lastCursorSeq) {
        if (first) { ghost.style.transition = "none"; }
        ghost.style.transform = `translate(${c.x}px, ${c.y}px)`;
        if (first) { void ghost.offsetWidth; ghost.style.transition = ""; }
        lastCursorSeq = c.seq;
      }
      ghost.style.opacity = String(c.alpha);
    }
    // keep animating only while something time-dependent is on screen (fades, ghost idle fade); a drawing in progress re-renders on pointer events
    if (view.route && view.route.phase !== "active" || view.cursor || view.strokes && view.strokes.settled) raf = requestAnimationFrame(() => setTimeout(render, 80));
  }

  // ---------- calibration (drag the rectangle; drag a corner to resize; the frame stays square) ----------
  let calib = null;
  function setupCalibration() {
    const bounds = { x: 0, y: 0, width: layout.w, height: layout.h };
    const origin = layout.origin || { x: 0, y: 0 };
    const toLocal = r => ({ x: r.x - origin.x, y: r.y - origin.y, w: r.w, h: r.h });
    calib = { bounds, origin, rect: G.fitRect(toLocal(layout.rect), bounds), defaultRect: toLocal(layout.defaultRect), drag: null };
    paintCalibration();
  }
  function paintCalibration() {
    if (!calib) return;
    const el = $("calib-rect");
    for (const [k, v] of [["left", calib.rect.x], ["top", calib.rect.y], ["width", calib.rect.w], ["height", calib.rect.h]]) el.style[k] = `${v}px`;
    $("calib-text").textContent = `미니맵 위치 ${Math.round(calib.rect.w)}px — 끌어서 맞추고 저장`;
  }
  $("calib-rect").addEventListener("pointerdown", event => {
    if (!calib || mode !== "calibrate") return;
    const corner = event.target && event.target.dataset && event.target.dataset.corner;
    calib.drag = { corner: corner || "", start: { x: event.clientX, y: event.clientY }, rect: { ...calib.rect }, id: event.pointerId };
    try { $("calib-rect").setPointerCapture(event.pointerId); } catch { /* optional */ }
    event.preventDefault();
  });
  $("calib-rect").addEventListener("pointermove", event => {
    if (!calib || !calib.drag || event.pointerId !== calib.drag.id) return;
    const d = calib.drag;
    calib.rect = d.corner ? G.resizeFromCorner(d.rect, d.corner, { x: event.clientX, y: event.clientY }, calib.bounds) : G.moveRect(d.rect, event.clientX - d.start.x, event.clientY - d.start.y, calib.bounds);
    paintCalibration();
  });
  const endDrag = event => { if (calib && calib.drag && event.pointerId === calib.drag.id) calib.drag = null; };
  $("calib-rect").addEventListener("pointerup", endDrag); $("calib-rect").addEventListener("pointercancel", endDrag);
  $("calib-save").addEventListener("click", () => { if (calib) api.calibration("save", { x: calib.rect.x + calib.origin.x, y: calib.rect.y + calib.origin.y, w: calib.rect.w, h: calib.rect.h }); });
  $("calib-default").addEventListener("click", () => { if (calib) { calib.rect = G.fitRect(calib.defaultRect, calib.bounds); paintCalibration(); } });
  $("calib-cancel").addEventListener("click", () => api.calibration("cancel"));
})();
