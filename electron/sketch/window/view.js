"use strict";
// Pure scene builder for the sketch window: route view-model (route_model.js) + layout -> a plain node tree
// ({tag, attrs, children, text}) that sketch.js materialises as SVG. No DOM here, so every drawn element is unit-testable.
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory(require("../strokes.js"));
  else root.VoiSketchView = factory(root.VoiSketchStrokes);
})(typeof self !== "undefined" ? self : this, function (Strokes) {
  const num = v => String(Math.round(v * 10) / 10);
  const node = (tag, attrs = {}, children = [], text) => ({ tag, attrs, children, ...(text !== undefined ? { text } : {}) });
  const toPx = (layout, nx, ny) => ({ x: Math.round((layout.ox + nx * layout.w) * 10) / 10, y: Math.round((layout.oy + ny * layout.h) * 10) / 10 });
  const pts = list => list.map(p => `${num(p.x)},${num(p.y)}`).join(" ");

  // Where a label goes: right of the point, or left of it when it would run out of the window.
  function labelAnchor(layout, nx, ny, textWidth) {
    const p = toPx(layout, nx, ny);
    if (p.x + 14 + textWidth > layout.width) return { x: p.x - 14, y: p.y - 12 < 10 ? p.y + 16 : p.y - 12, anchor: "end" };
    return { x: p.x + 14, y: p.y - 12 < 10 ? p.y + 16 : p.y - 12, anchor: "start" };
  }
  const textWidth = label => Math.min(160, String(label).length * 11 + 6);

  function strokeLayer(strokes, alpha, layout) {
    const children = [];
    for (const stroke of strokes) {
      if (stroke.length < 2) continue;
      const px = stroke.map(p => toPx(layout, p[0], p[1]));
      const points = pts(px);
      children.push(node("polyline", { class: "stroke-under", points }), node("polyline", { class: "stroke", points }));
      for (const c of Strokes.corners(stroke)) { const p = toPx(layout, c.nx, c.ny); children.push(node("circle", { class: "corner-dot", cx: num(p.x), cy: num(p.y), r: "3.4" })); }
      const dir = Strokes.endDirection(stroke);
      if (dir) {
        const last = px[px.length - 1];
        const tip = { x: last.x + dir.dx * 10, y: last.y + dir.dy * 10 }, base = { x: last.x - dir.dx * 3, y: last.y - dir.dy * 3 };
        const nx = -dir.dy * 6.5, ny = dir.dx * 6.5;
        children.push(node("polygon", { class: "arrow", points: pts([tip, { x: base.x + nx, y: base.y + ny }, { x: base.x - nx, y: base.y - ny }]) }));
      }
    }
    return node("g", { class: "layer-strokes", opacity: String(alpha) }, children);
  }

  const SWORD = [["M-6 6 L5 -5", "stroke"], ["M2 -8 L8 -8 L8 -2", "stroke"], ["M-8 4 L-4 8 M-9 9 L-6 6", "stroke"]];
  function finalIcon(kind) {
    if (kind === "sword") return [node("path", { class: "final-icon", d: "M-6 6 L6 -6 M3 -7 L7 -7 L7 -3 M-9 3 L-3 9 M-8 8 L-5 5" })];
    return [node("path", { class: "final-icon", d: "M-4 8 V-8 L7 -4 L-4 0" }), node("path", { class: "final-icon", d: "M-4 -8 V-8", "stroke-width": "2.4" })];
  }
  function routeLayer(route, layout) {
    const children = [];
    const all = route.polyline.map(p => toPx(layout, p.nx, p.ny));
    children.push(node("polyline", { class: "route-line", points: pts(all) }));
    const reachedPts = route.waypoints.filter(w => w.reached).map(w => toPx(layout, w.nx, w.ny));
    if (route.final.reached) reachedPts.push(toPx(layout, route.final.nx, route.final.ny));
    if (reachedPts.length) children.push(node("polyline", { class: "route-line-done", points: pts(reachedPts) }));
    for (const w of route.waypoints) {
      const p = toPx(layout, w.nx, w.ny);
      const a = labelAnchor(layout, w.nx, w.ny, textWidth(w.label));
      children.push(node("g", { class: "wp", "data-reached": String(w.reached), "data-next": String(w.next), "data-number": String(w.number) }, [
        node("circle", { class: "wp-disc", cx: num(p.x), cy: num(p.y), r: "8.5" }),
        node("text", { class: "wp-num", x: num(p.x), y: num(p.y + 0.5) }, [], String(w.number)),
        node("text", { class: "wp-label", x: num(a.x), y: num(a.y), "text-anchor": a.anchor }, [], w.label),
      ]));
    }
    const f = route.final, fp = toPx(layout, f.nx, f.ny), fa = labelAnchor(layout, f.nx, f.ny, textWidth(f.label));
    children.push(node("g", { class: "final", "data-action": f.action, "data-icon": f.marker, "data-reached": String(f.reached), "data-next": String(f.next) }, [
      node("circle", { class: "final-disc", cx: num(fp.x), cy: num(fp.y), r: "12" }),
      node("g", { transform: `translate(${num(fp.x)} ${num(fp.y)})` }, finalIcon(f.marker)),
      node("text", { class: "final-label", x: num(fa.x), y: num(fa.y), "text-anchor": fa.anchor }, [], f.label),
    ]));
    return node("g", { class: "layer-route", opacity: String(Math.round(route.alpha * 1000) / 1000), "data-phase": route.phase }, children);
  }

  // view: route_model.view(); live: points of the stroke being drawn right now (or null)
  function buildScene({ view, layout, live = null }) {
    const children = [];
    if (view.route) children.push(routeLayer(view.route, layout));
    const drawn = [];
    let alpha = 1;
    if (view.strokes) { drawn.push(...view.strokes.strokes); alpha = view.strokes.alpha; }
    if (live && live.length) drawn.push(live);
    if (drawn.length) children.push(strokeLayer(drawn, live && live.length ? 1 : alpha, layout));
    return node("g", { class: "scene" }, children);
  }
  function cursorPlacement(cursor, layout) {
    if (!cursor) return null;
    const p = toPx(layout, cursor.nx, cursor.ny);
    return { x: p.x, y: p.y, action: cursor.action, label: cursor.label, alpha: cursor.alpha, seq: cursor.seq };
  }

  // Materialise a node tree as SVG (browser only). Attributes only: no inline style attribute and no markup parsing (the window CSP forbids inline styles).
  function materialize(doc, tree) {
    const el = doc.createElementNS("http://www.w3.org/2000/svg", tree.tag);
    for (const [k, v] of Object.entries(tree.attrs)) el.setAttribute(k, v);
    if (tree.text !== undefined) el.textContent = tree.text;
    for (const child of tree.children) el.append(materialize(doc, child));
    return el;
  }
  return { toPx, labelAnchor, buildScene, cursorPlacement, materialize, node };
});
