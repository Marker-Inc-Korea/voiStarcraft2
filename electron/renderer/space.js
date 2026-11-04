"use strict";
// Deep-space background: parallax starfield + slow drifting nebula blobs, all on one canvas.
// Honors prefers-reduced-motion (renders a single still frame) and pauses when the window is hidden.
(function () {
  const canvas = document.getElementById("space");
  const ctx = canvas.getContext("2d");
  const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
  let width = 0, height = 0, dpr = 1, raf = 0, last = 0;
  let stars = [];
  const NEBULAE = [
    { x: 0.18, y: 0.30, r: 0.55, h: 262, s: 0.8, l: 0.30, dx: 0.010, dy: 0.006 },
    { x: 0.82, y: 0.22, r: 0.50, h: 196, s: 0.9, l: 0.24, dx: -0.008, dy: 0.008 },
    { x: 0.55, y: 0.95, r: 0.60, h: 318, s: 0.7, l: 0.22, dx: 0.006, dy: -0.006 },
    { x: 0.95, y: 0.85, r: 0.40, h: 228, s: 0.9, l: 0.20, dx: -0.005, dy: -0.004 },
  ];
  function seedStars() {
    const count = Math.min(520, Math.round((width * height) / 4200));
    stars = Array.from({ length: count }, () => {
      const depth = Math.random();
      return { x: Math.random(), y: Math.random(), z: depth, r: 0.35 + depth * 1.15, tw: Math.random() * Math.PI * 2, ts: 0.4 + Math.random() * 1.4, hue: Math.random() < 0.18 ? 205 + Math.random() * 40 : Math.random() < 0.1 ? 20 + Math.random() * 30 : 0 };
    });
  }
  function resize() {
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    const box = canvas.parentElement.getBoundingClientRect(); width = Math.max(1, Math.round(box.width)); height = Math.max(1, Math.round(box.height));
    canvas.width = Math.round(width * dpr); canvas.height = Math.round(height * dpr);
    canvas.style.width = `${width}px`; canvas.style.height = `${height}px`;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    seedStars();
    draw(performance.now());
  }
  function draw(time) {
    const t = time / 1000;
    // Transparent canvas: nebula/stars are drawn additively so they tint the glass without making it opaque.
    ctx.globalCompositeOperation = "source-over";
    ctx.clearRect(0, 0, width, height);
    ctx.globalCompositeOperation = "lighter";
    const span = Math.max(width, height);
    for (const n of NEBULAE) {
      const cx = (n.x + Math.sin(t * n.dx * 6.28 + n.h) * 0.06) * width;
      const cy = (n.y + Math.cos(t * n.dy * 6.28 + n.h) * 0.06) * height;
      const radius = n.r * span;
      const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, radius);
      g.addColorStop(0, `hsla(${n.h}, ${n.s * 100}%, ${n.l * 100}%, 0.34)`);
      g.addColorStop(0.45, `hsla(${n.h + 18}, ${n.s * 90}%, ${n.l * 70}%, 0.14)`);
      g.addColorStop(1, `hsla(${n.h}, 80%, 10%, 0)`);
      ctx.fillStyle = g; ctx.fillRect(0, 0, width, height);
    }
    ctx.globalCompositeOperation = "source-over";
    const drift = motion.matches ? 0 : t;
    for (const s of stars) {
      const px = ((s.x + drift * 0.004 * (0.2 + s.z)) % 1 + 1) % 1;
      const x = px * width, y = s.y * height;
      const twinkle = motion.matches ? 0.85 : 0.65 + 0.35 * Math.sin(t * s.ts + s.tw);
      const alpha = (0.35 + s.z * 0.6) * twinkle;
      ctx.fillStyle = s.hue ? `hsla(${s.hue}, 90%, 82%, ${alpha})` : `rgba(235, 240, 255, ${alpha})`;
      ctx.beginPath(); ctx.arc(x, y, s.r, 0, 6.2832); ctx.fill();
      if (s.z > 0.93) {
        ctx.fillStyle = `rgba(190, 210, 255, ${alpha * 0.18})`;
        ctx.beginPath(); ctx.arc(x, y, s.r * 4.2, 0, 6.2832); ctx.fill();
      }
    }
  }
  function frame(time) {
    raf = 0;
    if (document.hidden || motion.matches || document.body.dataset.revealed !== "true") { raf = 0; return; }
    if (time - last >= 33) { last = time; draw(time); } // ~30 fps is plenty for a slow backdrop
    raf = requestAnimationFrame(frame);
  }
  function kick() { if (!raf && !document.hidden && !motion.matches) raf = requestAnimationFrame(frame); else if (motion.matches) draw(performance.now()); }
  new ResizeObserver(resize).observe(canvas.parentElement);
  document.addEventListener("visibilitychange", kick);
  new MutationObserver(kick).observe(document.body, { attributes: true, attributeFilter: ["data-revealed"] });
  motion.addEventListener("change", kick);
  resize(); kick();
})();
