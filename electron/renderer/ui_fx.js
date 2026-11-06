"use strict";
// Tiny presentation helper: keeps the filled part of every settings slider in sync (--p on the range input) so the track can be
// drawn as an Apple-style filled bar. Pure math is exported for tests; the DOM wiring only runs in the renderer.
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.VoiUiFx = factory(root);
})(typeof self !== "undefined" ? self : this, function (root) {
  // Percentage (0..100, one decimal) of the track that is filled for a range input's min/max/value.
  function fillPercent(min, max, value) {
    const lo = Number(min), hi = Number(max), v = Number(value);
    if (![lo, hi, v].every(Number.isFinite) || hi <= lo) return 0;
    return Math.round(Math.min(1, Math.max(0, (v - lo) / (hi - lo))) * 1000) / 10;
  }
  const paint = input => input.style.setProperty("--p", `${fillPercent(input.min || 0, input.max || 100, input.value)}%`);

  function install(doc) {
    const rows = doc.getElementById("rows");
    if (!rows) return;
    const sweep = () => rows.querySelectorAll('input[type="range"]').forEach(paint);
    rows.addEventListener("input", event => { if (event.target && event.target.type === "range") paint(event.target); });
    new MutationObserver(sweep).observe(rows, { childList: true });
    sweep();
  }
  if (root && root.document && typeof MutationObserver !== "undefined") install(root.document);
  return { fillPercent };
});
