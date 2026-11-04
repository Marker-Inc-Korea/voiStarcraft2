"use strict";
// Starfield + nebula inside the glass panel only. Transparent canvas, drawn additively so it tints the glass without
// making it opaque. Density / speed / nebula / hue are live settings (VoiSpace.configure). Honors reduced motion,
// pauses while the panel is hidden or the window is not visible.
(function () {
  const canvas = document.getElementById("space");
  const ctx = canvas.getContext("2d");
  const osMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  let width = 0, height = 0, dpr = 1, raf = 0, last = 0;
  let stars = [];
  let cfg = { stars: 0.5, speed: 0.4, nebula: 0.5, hue: 250, saturation: 0.55, still: false };
  const still = () => osMotion.matches || cfg.still;
  const NEBULAE = [
    { x: 0.18, y: 0.30, r: 0.55, dh: 12, l: 0.30, dx: 0.010, dy: 0.006 },
    { x: 0.82, y: 0.22, r: 0.50, dh: -54, l: 0.24, dx: -0.008, dy: 0.008 },
    { x: 0.55, y: 0.95, r: 0.60, dh: 68, l: 0.22, dx: 0.006, dy: -0.006 },
    { x: 0.95, y: 0.85, r: 0.40, dh: -22, l: 0.20, dx: -0.005, dy: -0.004 },
  ];
  function seedStars() {
    const count = Math.min(520, Math.round((width * height) / 4200 * (0.2 + cfg.stars * 1.6)));
    stars = Array.from({ length: count }, () => {
      const depth = Math.random();
      return { x: Math.random(), y: Math.random(), z: depth, r: 0.35 + depth * 1.15, tw: Math.random() * Math.PI * 2, ts: 0.4 + Math.random() * 1.4, hue: Math.random() < 0.18 ? 205 + Math.random() * 40 : Math.random() < 0.1 ? 20 + Math.random() * 30 : 0 };
    });
  }
  function resize() {
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    const box = canvas.parentElement.getBoundingClientRect();
    width = Math.max(1, Math.round(box.width)); height = Math.max(1, Math.round(box.height));
    canvas.width = Math.round(width * dpr); canvas.height = Math.round(height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    seedStars();
    draw(performance.now());
  }
  function draw(time) {
    const t = time / 1000 * (0.2 + cfg.speed * 1.8);
    ctx.globalCompositeOperation = "source-over";
    ctx.clearRect(0, 0, width, height);
    ctx.globalCompositeOperation = "lighter";
    const span = Math.max(width, height);
    const strength = cfg.nebula;
    if (strength > 0.01) for (const n of NEBULAE) {
      const hue = (cfg.hue + n.dh + 360) % 360;
      const cx = (n.x + Math.sin(t * n.dx * 6.28 + hue) * 0.06) * width;
      const cy = (n.y + Math.cos(t * n.dy * 6.28 + hue) * 0.06) * height;
      const radius = n.r * span;
      const sat = Math.round(30 + cfg.saturation * 65);
      const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, radius);
      g.addColorStop(0, `hsla(${hue}, ${sat}%, ${n.l * 100}%, ${0.34 * strength * 1.6})`);
      g.addColorStop(0.45, `hsla(${hue + 18}, ${sat * 0.9}%, ${n.l * 70}%, ${0.14 * strength * 1.6})`);
      g.addColorStop(1, `hsla(${hue}, 80%, 10%, 0)`);
      ctx.fillStyle = g; ctx.fillRect(0, 0, width, height);
    }
    ctx.globalCompositeOperation = "source-over";
    const drift = still() ? 0 : t;
    for (const s of stars) {
      const px = ((s.x + drift * 0.004 * (0.2 + s.z)) % 1 + 1) % 1;
      const x = px * width, y = s.y * height;
      const twinkle = still() ? 0.85 : 0.65 + 0.35 * Math.sin(t * s.ts + s.tw);
      const alpha = (0.35 + s.z * 0.6) * twinkle;
      ctx.fillStyle = s.hue ? `hsla(${s.hue}, 90%, 82%, ${alpha})` : `rgba(235, 240, 255, ${alpha})`;
      ctx.beginPath(); ctx.arc(x, y, s.r, 0, 6.2832); ctx.fill();
      if (s.z > 0.93) { ctx.fillStyle = `rgba(190, 210, 255, ${alpha * 0.18})`; ctx.beginPath(); ctx.arc(x, y, s.r * 4.2, 0, 6.2832); ctx.fill(); }
    }
  }
  function frame(time) {
    raf = 0;
    if (document.hidden || still() || document.body.dataset.revealed !== "true") return;
    if (time - last >= 33) { last = time; draw(time); } // ~30 fps is plenty for a slow backdrop
    raf = requestAnimationFrame(frame);
  }
  function kick() { if (still()) { draw(performance.now()); return; } if (!raf && !document.hidden) raf = requestAnimationFrame(frame); }
  function configure(next) {
    const reseed = next.stars !== undefined && next.stars !== cfg.stars;
    cfg = { ...cfg, ...next };
    if (reseed) seedStars();
    draw(performance.now()); kick();
  }
  new ResizeObserver(resize).observe(canvas.parentElement);
  document.addEventListener("visibilitychange", kick);
  osMotion.addEventListener("change", kick);
  new MutationObserver(kick).observe(document.body, { attributes: true, attributeFilter: ["data-revealed"] });
  window.VoiSpace = { configure };
  resize(); kick();
})();
