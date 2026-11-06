"use strict";
// Pure skin model: which skins exist and the CSS variables derived from the glass settings. Both skins stay translucent
// (opacity/blur are the same two settings); the terminal skin additionally computes its text scrim and glow from the
// opacity so green-on-black stays legible when the panel is almost clear.
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.VoiSkins = factory();
})(typeof self !== "undefined" ? self : this, function () {
  const SKINS = Object.freeze(["cosmic", "terminal"]);
  const DEFAULT_SKIN = "cosmic";
  // Corner smoothing per skin (0 = circular arc, 1 = most continuous "squircle"): every corner of every skin is rounded;
  // the terminal keeps its green/black monospace look as colour and effect only, never as angular geometry.
  const SMOOTH = Object.freeze({ cosmic: 0.6, terminal: 0.45 });
  const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
  const fixed = (value, digits = 2) => value.toFixed(digits);

  const normalize = skin => (SKINS.includes(skin) ? skin : DEFAULT_SKIN);
  const smoothFor = skin => SMOOTH[normalize(skin)];

  // alpha = glass.alpha (0.08..0.9). Lower alpha = more see-through = stronger text halo / line scrim.
  function legibility(skin, alpha) {
    const clear = 1 - clamp(alpha, 0, 1);
    if (normalize(skin) === "terminal") return { legible: fixed(clamp(0.6 + clear * 0.5, 0, 0.98)), scrim: fixed(clamp(0.4 + clear * 0.42, 0.4, 0.82)), glow: fixed(clamp(0.5 + clear * 0.35, 0.5, 0.85)) };
    return { legible: fixed(Math.min(0.95, 0.45 + clear * 0.6)), scrim: fixed(0.22 + clear * 0.3), glow: "0.50" };
  }

  // CSS custom properties for the HUD root, as a plain map (renderer applies them; tests inspect them).
  function cssVars(skin, glass) {
    const key = normalize(skin);
    const { legible, scrim, glow } = legibility(key, glass.alpha);
    return {
      "--glass-alpha": String(glass.alpha), "--glass-blur": `${glass.blur}px`, "--legible": legible, "--scrim": scrim, "--term-glow": glow,
      "--hue": String(glass.hue), "--sat": `${glass.saturation}%`, "--border-glow": String(glass.borderGlow / 100),
      "--radius": `${glass.radius}px`, "--text": `${glass.textSize}px`,
    };
  }

  return { SKINS, DEFAULT_SKIN, SMOOTH, normalize, smoothFor, legibility, cssVars };
});
