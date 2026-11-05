"use strict";
// Cosmic layers inside the glass panel, drawn on ONE transparent canvas with additive blending so they tint the glass
// without making it opaque: 3-depth parallax starfield, 4-point flares, shooting stars, faint constellation lines, soft
// drifting nebula clouds (60-90 s loop) and a thin aurora shimmer along the top edge.
// One rAF loop capped at ~30 fps; fully paused while the bar is hidden; a single still frame under reduced motion.
// Star density / speed / nebula / twinkle / meteors are live settings (VoiSpace.configure), independent of glass opacity.
(function () {
  const Sky = window.VoiSky;
  const canvas = document.getElementById("space");
  const ctx = canvas.getContext("2d");
  const osMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  let width = 0, height = 0, dpr = 1, raf = 0, last = 0, started = performance.now();
  let stars = [], edges = [];
  let meteor = null, meteorStart = 0, nextMeteorAt = 0;
  const rand = Sky.mulberry32(0x5eed);
  let cfg = { enabled: true, stars: 0.5, speed: 0.4, nebula: 0.5, hue: 250, saturation: 0.55, twinkle: true, meteors: true, still: false, glassAlpha: 0.28 };
  const still = () => osMotion.matches || cfg.still;
  const visible = () => cfg.enabled !== false && document.body.dataset.revealed === "true" && !document.hidden;

  // Nebula clouds: violet / indigo / cyan / magenta, offsets from the user hue so the 색조 slider recolours the sky.
  const CLOUDS = [
    { x: 0.14, y: 0.35, r: 0.62, dh: 10, l: 0.34, ax: 0.07, ay: 0.05, ps: 1.0 },
    { x: 0.80, y: 0.20, r: 0.55, dh: -60, l: 0.30, ax: 0.06, ay: 0.07, ps: 0.8 },
    { x: 0.52, y: 0.98, r: 0.66, dh: 72, l: 0.28, ax: 0.08, ay: 0.04, ps: 1.2 },
    { x: 0.96, y: 0.82, r: 0.44, dh: -20, l: 0.26, ax: 0.05, ay: 0.06, ps: 0.9 },
    { x: 0.40, y: 0.10, r: 0.38, dh: 130, l: 0.22, ax: 0.07, ay: 0.05, ps: 1.1 },
  ];
  const LOOP_SECONDS = 75; // one full drift cycle of the clouds

  function seed() {
    const count = Sky.starCount(width, height, cfg.stars);
    stars = Sky.layout(count, 7);
    edges = Sky.constellation(stars, { points: 6, maxLength: 0.3 });
  }
  function resize() {
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    const box = canvas.parentElement.getBoundingClientRect();
    width = Math.max(1, Math.round(box.width)); height = Math.max(1, Math.round(box.height));
    canvas.width = Math.round(width * dpr); canvas.height = Math.round(height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    seed();
    draw(performance.now());
  }

  function drawNebula(t, k) {
    if (k.nebula <= 0.01) return;
    const span = Math.max(width, height);
    const sat = Math.round(35 + cfg.saturation * 60);
    const phase = (t / LOOP_SECONDS) * Math.PI * 2;
    for (const c of CLOUDS) {
      const hue = (cfg.hue + c.dh + 360) % 360;
      const cx = (c.x + Math.sin(phase * c.ps + c.dh) * c.ax) * width;
      const cy = (c.y + Math.cos(phase * c.ps * 0.8 + c.dh) * c.ay) * height;
      const radius = c.r * span;
      // Two stacked radial gradients per cloud: a wide soft body and a tighter bright core (layered, blurred look).
      const body = ctx.createRadialGradient(cx, cy, 0, cx, cy, radius);
      body.addColorStop(0, `hsla(${hue}, ${sat}%, ${c.l * 100}%, ${0.40 * k.nebula})`);
      body.addColorStop(0.5, `hsla(${hue + 20}, ${sat * 0.9}%, ${c.l * 62}%, ${0.17 * k.nebula})`);
      body.addColorStop(1, `hsla(${hue}, 80%, 10%, 0)`);
      ctx.fillStyle = body; ctx.fillRect(0, 0, width, height);
      const core = ctx.createRadialGradient(cx, cy, 0, cx, cy, radius * 0.38);
      core.addColorStop(0, `hsla(${hue - 14}, ${sat}%, ${c.l * 130}%, ${0.22 * k.nebula})`);
      core.addColorStop(1, `hsla(${hue}, 80%, 20%, 0)`);
      ctx.fillStyle = core; ctx.fillRect(0, 0, width, height);
    }
  }

  // Aurora / cosmic-dust shimmer hugging the panel's top edge: a slow horizontal ribbon of two hues.
  function drawAurora(t, k) {
    if (k.aurora <= 0.02) return;
    const h = Math.min(height * 0.45, 70);
    const shift = still() ? 0.3 : (t * 0.018) % 1;
    const g = ctx.createLinearGradient(0, 0, width, 0);
    const hue = cfg.hue;
    for (let i = 0; i <= 6; i += 1) {
      const p = i / 6;
      const wave = 0.5 + 0.5 * Math.sin((p + shift) * Math.PI * 2);
      g.addColorStop(p, `hsla(${(hue + 80 * wave + 330) % 360}, 90%, 62%, ${(0.05 + 0.13 * wave) * k.aurora})`);
    }
    const v = ctx.createLinearGradient(0, 0, 0, h);
    v.addColorStop(0, "rgba(255,255,255,1)"); v.addColorStop(1, "rgba(255,255,255,0)");
    ctx.save();
    ctx.fillStyle = g; ctx.fillRect(0, 0, width, h);
    ctx.globalCompositeOperation = "destination-in"; // fade the ribbon out toward the middle
    ctx.fillStyle = v; ctx.fillRect(0, 0, width, h);
    ctx.restore();
  }

  function flare(x, y, size, a, hue) {
    const color = hue < 0 ? "220, 232, 255" : `${Math.round(200 + (hue % 60))}, 220, 255`;
    ctx.save(); ctx.translate(x, y);
    const g = ctx.createRadialGradient(0, 0, 0, 0, 0, size * 3.4);
    g.addColorStop(0, `rgba(${color}, ${0.32 * a})`); g.addColorStop(1, `rgba(${color}, 0)`);
    ctx.fillStyle = g; ctx.beginPath(); ctx.arc(0, 0, size * 3.4, 0, 6.2832); ctx.fill();
    // 4-point diamond flare
    ctx.fillStyle = `rgba(255,255,255,${0.85 * a})`;
    const L = size * 5.2, W = size * 0.55;
    ctx.beginPath(); ctx.moveTo(0, -L); ctx.lineTo(W, 0); ctx.lineTo(0, L); ctx.lineTo(-W, 0); ctx.closePath(); ctx.fill();
    ctx.beginPath(); ctx.moveTo(-L * 0.78, 0); ctx.lineTo(0, W); ctx.lineTo(L * 0.78, 0); ctx.lineTo(0, -W); ctx.closePath(); ctx.fill();
    ctx.restore();
  }

  function drawStars(t, k) {
    if (k.star <= 0) return;
    const live = !still();
    const motion = live ? t : 0;
    const pos = new Map();
    for (const s of stars) {
      const x = Sky.driftX(s, motion, cfg.speed) * width, y = s.y * height;
      pos.set(s, [x, y]);
      const a = Math.min(1, s.alpha * Sky.twinkle(s, t, cfg.twinkle && live) * k.star);
      ctx.fillStyle = s.hue < 0 ? `rgba(236, 242, 255, ${a})` : `hsla(${s.hue}, 90%, 82%, ${a})`;
      ctx.beginPath(); ctx.arc(x, y, s.r, 0, 6.2832); ctx.fill();
      if (s.depth === "near") { // soft halo on the bright ones
        ctx.fillStyle = s.hue < 0 ? `rgba(190, 210, 255, ${a * 0.2})` : `hsla(${s.hue}, 90%, 78%, ${a * 0.2})`;
        ctx.beginPath(); ctx.arc(x, y, s.r * 4.4, 0, 6.2832); ctx.fill();
      }
    }
    // constellation lines between a few bright stars (very faint)
    if (edges.length) {
      ctx.lineWidth = 0.6; ctx.strokeStyle = `rgba(170, 190, 255, ${0.16 * k.star})`;
      ctx.beginPath();
      for (const [a, b] of edges) { const p = pos.get(a), q = pos.get(b); if (!p || !q) continue; if (Math.abs(p[0] - q[0]) > width * 0.5) continue; ctx.moveTo(p[0], p[1]); ctx.lineTo(q[0], q[1]); }
      ctx.stroke();
    }
    for (const s of stars) if (s.flare) { const p = pos.get(s); const a = Math.min(1, s.alpha * Sky.twinkle(s, t, cfg.twinkle && live) * k.star); flare(p[0], p[1], s.r * 0.75, a, s.hue); }
  }

  function drawMeteor(now, k) {
    if (!cfg.meteors || still() || k.star <= 0) { meteor = null; return; }
    if (!meteor) {
      if (now >= nextMeteorAt) { meteor = Sky.newMeteor(rand); meteorStart = now; }
      return;
    }
    const life = (now - meteorStart) / meteor.duration;
    if (life >= 1) { meteor = null; nextMeteorAt = now + Sky.nextMeteorDelay(rand); return; }
    const p = Sky.meteorPoint(meteor, life);
    const g = ctx.createLinearGradient(p.tailX * width, p.tailY * height, p.x * width, p.y * height);
    g.addColorStop(0, "rgba(180,200,255,0)"); g.addColorStop(1, `rgba(255,255,255,${0.9 * p.alpha})`);
    ctx.strokeStyle = g; ctx.lineWidth = 1.4; ctx.lineCap = "round";
    ctx.beginPath(); ctx.moveTo(p.tailX * width, p.tailY * height); ctx.lineTo(p.x * width, p.y * height); ctx.stroke();
    ctx.fillStyle = `rgba(255,255,255,${p.alpha})`; ctx.beginPath(); ctx.arc(p.x * width, p.y * height, 1.3, 0, 6.2832); ctx.fill();
  }

  function draw(now) {
    const t = (now - started) / 1000;
    const k = Sky.intensity({ stars: cfg.stars, nebula: cfg.nebula, glassAlpha: cfg.glassAlpha });
    ctx.globalCompositeOperation = "source-over";
    ctx.clearRect(0, 0, width, height);
    ctx.globalCompositeOperation = "lighter"; // additive: never darkens, never makes the glass opaque
    drawNebula(t, k);
    drawAurora(t, k);
    drawStars(t, k);
    drawMeteor(now, k);
    ctx.globalCompositeOperation = "source-over";
  }

  function frame(time) {
    raf = 0;
    if (!visible() || still()) return; // paused while the bar is collapsed/hidden
    if (Sky.shouldDraw({ now: time, last, visible: true, still: false })) { last = time; draw(time); }
    raf = requestAnimationFrame(frame);
  }
  function kick() {
    if (still()) { draw(performance.now()); return; }
    if (!raf && visible()) { nextMeteorAt = Math.max(nextMeteorAt, performance.now() + 2500); raf = requestAnimationFrame(frame); }
  }
  function configure(next) {
    const reseed = (next.stars !== undefined && next.stars !== cfg.stars);
    cfg = { ...cfg, ...next };
    if (reseed) seed();
    draw(performance.now()); kick();
  }
  new ResizeObserver(resize).observe(canvas.parentElement);
  document.addEventListener("visibilitychange", kick);
  osMotion.addEventListener("change", kick);
  new MutationObserver(kick).observe(document.body, { attributes: true, attributeFilter: ["data-revealed"] });
  window.VoiSpace = { configure, get running() { return raf !== 0; } };
  resize(); kick();
})();
