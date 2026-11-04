"use strict";
(function () {
  const states = new Set(["off", "listening", "thinking"]);
  const glow = document.getElementById("glow");
  const bloom = document.getElementById("bloom");
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
  let smooth = 0;
  let reactive = true, intensity = 0.95;
  const root = document.documentElement.style;
  // Overlay reads its own subset of the settings (depth, intensity, speed, brightness, reactive).
  window.overlay.onSettings(s => {
    if (!s || typeof s !== "object") return;
    reactive = s.reactive !== false;
    intensity = Math.min(1, Math.max(0.1, (Number(s.intensity) || 95) / 100));
    root.setProperty("--edge-depth", `${Math.min(240, Math.max(30, Number(s.depth) || 90))}px`);
    root.setProperty("--intensity", String(intensity));
    root.setProperty("--brightness", String(Math.min(1.5, Math.max(0.5, (Number(s.brightness) || 100) / 100))));
    const loop = `${Math.min(30, Math.max(3, Number(s.loopSeconds) || 6))}s`;
    root.setProperty("--loop", loop); root.setProperty("--base-loop", loop);
    document.body.dataset.motion = s.reduceMotion ? "off" : "on";
  });
  window.overlay.onState(payload => {
    if (!payload || !states.has(payload.state)) return;
    document.body.dataset.state = payload.state;
    if (payload.state !== "listening") { smooth = 0; glow.style.opacity = ""; bloom.style.opacity = ""; }
  });
  // Voice level 0..1 (~20 Hz from the mic). Smoothed here and written as plain opacity: compositor-only, no layout.
  window.overlay.onLevel(level => {
    if (!reactive || reduced.matches || document.body.dataset.motion === "off" || document.body.dataset.state !== "listening" || typeof level !== "number") return;
    const target = Math.min(1, Math.max(0, level));
    smooth += (target - smooth) * (target > smooth ? 0.55 : 0.22);
    glow.style.opacity = String(intensity * (0.75 + 0.25 * smooth));
    bloom.style.opacity = String(intensity * (0.27 + 0.33 * smooth));
  });
})();
