"use strict";
(function () {
  const states = new Set(["off", "listening", "thinking"]);
  const glow = document.getElementById("glow");
  const bloom = document.getElementById("bloom");
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
  let smooth = 0;
  window.overlay.onState(payload => {
    if (!payload || !states.has(payload.state)) return;
    document.body.dataset.state = payload.state;
    if (payload.state !== "listening") { smooth = 0; glow.style.opacity = ""; bloom.style.opacity = ""; }
  });
  // Voice level 0..1 (~20 Hz from the mic). Smoothed here and written as plain opacity: compositor-only, no layout.
  window.overlay.onLevel(level => {
    if (reduced.matches || document.body.dataset.state !== "listening" || typeof level !== "number") return;
    const target = Math.min(1, Math.max(0, level));
    smooth += (target - smooth) * (target > smooth ? 0.55 : 0.22);
    glow.style.opacity = String(0.72 + 0.28 * smooth);
    bloom.style.opacity = String(0.26 + 0.34 * smooth);
  });
})();
