"use strict";
// Pure sky model for the cosmic glass: star layout, constellation edges, meteor schedule, twinkle. No DOM, no canvas,
// no Math.random in the hot path (a seeded PRNG makes layouts reproducible and unit-testable).
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.VoiSky = factory();
})(typeof self !== "undefined" ? self : this, function () {
  // Three depths: far (tiny, dim, slow), mid, near (bright, big, fast parallax). `share` sums to 1.
  const DEPTHS = Object.freeze([
    Object.freeze({ id: "far", share: 0.62, radius: [0.35, 0.6], alpha: [0.22, 0.45], parallax: 0.35 }),
    Object.freeze({ id: "mid", share: 0.30, radius: [0.6, 1.0], alpha: [0.4, 0.7], parallax: 0.7 }),
    Object.freeze({ id: "near", share: 0.08, radius: [1.0, 1.7], alpha: [0.7, 1.0], parallax: 1.2 }),
  ]);
  const MAX_STARS = 420;
  const METEOR_MIN_MS = 8000, METEOR_MAX_MS = 14000;

  function mulberry32(seed) {
    let a = seed >>> 0;
    return () => { a = (a + 0x6d2b79f5) >>> 0; let t = a; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
  }
  const lerp = (a, b, t) => a + (b - a) * t;
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

  // How many stars for a panel: scales with area and the 별 slider (0..1), capped for CPU.
  function starCount(width, height, density) {
    const base = (Math.max(0, width) * Math.max(0, height)) / 5200;
    return clamp(Math.round(base * (0.15 + 1.6 * clamp(density, 0, 1))), 0, MAX_STARS);
  }

  // Star list in unit space (x,y in 0..1) with a depth, size, base alpha, twinkle phase/speed, tint hue and flare flag.
  function layout(count, seed = 1) {
    const rand = mulberry32(seed);
    const stars = [];
    for (const depth of DEPTHS) {
      const n = Math.round(count * depth.share);
      for (let i = 0; i < n; i += 1) {
        const tint = rand();
        stars.push({
          depth: depth.id, parallax: depth.parallax,
          x: rand(), y: rand(),
          r: lerp(depth.radius[0], depth.radius[1], rand()),
          alpha: lerp(depth.alpha[0], depth.alpha[1], rand()),
          phase: rand() * Math.PI * 2, speed: lerp(0.5, 1.9, rand()),
          hue: tint < 0.16 ? 205 + rand() * 40 : tint < 0.24 ? 18 + rand() * 30 : tint < 0.30 ? 285 + rand() * 30 : -1, // -1 = white
          flare: false,
        });
      }
    }
    // A few of the brightest near stars get 4-point diamond flares (never more than 5, so the panel stays calm).
    const near = stars.filter(s => s.depth === "near").sort((a, b) => b.alpha - a.alpha);
    for (const s of near.slice(0, Math.min(5, near.length))) s.flare = true;
    return stars;
  }

  // Constellation: connect the brightest `points` near/mid stars into a faint path using nearest-neighbour chaining, with
  // a maximum edge length so lines stay short and local. Deterministic for a given layout.
  function constellation(stars, { points = 6, maxLength = 0.28 } = {}) {
    const bright = stars.filter(s => s.depth !== "far").sort((a, b) => b.alpha * b.r - a.alpha * a.r).slice(0, Math.max(0, points));
    if (bright.length < 2) return [];
    const used = new Set([0]);
    const edges = [];
    let current = bright[0];
    for (let step = 1; step < bright.length; step += 1) {
      let best = -1, bestDistance = Infinity;
      for (let i = 0; i < bright.length; i += 1) {
        if (used.has(i)) continue;
        const d = Math.hypot(bright[i].x - current.x, bright[i].y - current.y);
        if (d < bestDistance) { bestDistance = d; best = i; }
      }
      if (best < 0) break;
      if (bestDistance <= maxLength) edges.push([current, bright[best]]);
      used.add(best); current = bright[best];
    }
    return edges;
  }

  // Twinkle factor 0.55..1 (or constant 0.85 when twinkle is off / motion is reduced).
  function twinkle(star, seconds, enabled) {
    if (!enabled) return 0.85;
    return 0.775 + 0.225 * Math.sin(seconds * star.speed + star.phase);
  }

  // Parallax drift: near stars move faster than far ones; wraps in unit space. `speed` 0..1 is the 별 속도 slider.
  function driftX(star, seconds, speed) {
    const x = (star.x + seconds * 0.0035 * (0.2 + clamp(speed, 0, 1) * 1.8) * star.parallax) % 1;
    return x < 0 ? x + 1 : x;
  }

  // Meteor scheduler: the next shot happens METEOR_MIN..MAX ms after the previous one finished. Deterministic via rand.
  function nextMeteorDelay(rand) { return lerp(METEOR_MIN_MS, METEOR_MAX_MS, rand()); }
  // A meteor is a short streak in unit space with a life 0..1.
  function newMeteor(rand) {
    const fromLeft = rand() < 0.5;
    const y0 = rand() * 0.45;
    const angle = lerp(0.35, 0.75, rand()); // radians below horizontal
    return { x0: fromLeft ? rand() * 0.35 : 0.65 + rand() * 0.35, y0, dir: fromLeft ? 1 : -1, angle, length: lerp(0.12, 0.22, rand()), duration: lerp(650, 1100, rand()) };
  }
  function meteorPoint(meteor, life) {
    const l = clamp(life, 0, 1);
    const reach = 0.5 * l; // how far the head has travelled in unit width
    const x = meteor.x0 + meteor.dir * Math.cos(meteor.angle) * reach;
    const y = meteor.y0 + Math.sin(meteor.angle) * reach * 0.8;
    const fade = Math.sin(Math.PI * l);
    return { x, y, tailX: x - meteor.dir * Math.cos(meteor.angle) * meteor.length * fade, tailY: y - Math.sin(meteor.angle) * meteor.length * 0.8 * fade, alpha: fade };
  }

  // Intensity model: stars/nebula follow their OWN sliders, independent of the glass opacity, so the cosmos stays visible
  // at low glass alpha. 0 on a slider means off. `glassAlpha` only softens the nebula a little on a dense panel.
  function intensity({ stars = 0.5, nebula = 0.5, glassAlpha = 0.28 } = {}) {
    const a = clamp(glassAlpha, 0.08, 0.9);
    const s = clamp(stars, 0, 1), n = clamp(nebula, 0, 1);
    return { star: s === 0 ? 0 : 0.55 + 0.45 * s, nebula: n * (1.15 - 0.35 * ((a - 0.08) / 0.82)), aurora: n * 0.9 };
  }

  // Cheap frame budget: draw at most ~30 fps; skip entirely when not visible or motion is reduced.
  function shouldDraw({ now, last, visible, still, minInterval = 33 }) { return Boolean(visible) && !still && now - last >= minInterval; }

  return { DEPTHS, MAX_STARS, METEOR_MIN_MS, METEOR_MAX_MS, mulberry32, starCount, layout, constellation, twinkle, driftX, nextMeteorDelay, newMeteor, meteorPoint, intensity, shouldDraw };
});
