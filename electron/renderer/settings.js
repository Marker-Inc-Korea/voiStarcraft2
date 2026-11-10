"use strict";
// Pure settings schema: defaults, range-checked validation, sanitising of stored files, derived views.
// Loaded as a classic script in the renderer and as CommonJS by main/tests. No I/O.
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.VoiSettings = factory();
})(typeof self !== "undefined" ? self : this, function () {
  const DOCKS = Object.freeze(["bottom", "right", "left", "top"]);
  const ACCELERATOR = /^(?:(?:CommandOrControl|CmdOrCtrl|Command|Cmd|Control|Ctrl|Alt|Option|AltGr|Shift|Super|Meta)\+)+(?:[A-Z0-9]|F(?:[1-9]|1[0-9]|2[0-4])|Space|Tab|Backspace|Delete|Insert|Return|Enter|Up|Down|Left|Right|Home|End|PageUp|PageDown|Escape|Esc|[,./;'\[\]\\`=-])$/;
  const num = (min, max, def, extra = {}) => ({ type: "num", min, max, def, ...extra });
  const int = (min, max, def, extra = {}) => ({ type: "num", min, max, def, integer: true, ...extra });
  const bool = def => ({ type: "bool", def });
  const oneOf = (values, def) => ({ type: "enum", values, def });
  const text = (max, def, pattern) => ({ type: "str", max, def, pattern });

  // Order = order shown in the settings panel. `unit` is display-only; `scale` multiplies the stored value for display.
  const SCHEMA = Object.freeze({
    glass: {
      skin: oneOf(["cosmic", "terminal"], "cosmic"),
      alpha: num(0.08, 0.9, 0.28, { decimals: 2, unit: "%", scale: 100 }),
      blur: int(0, 40, 12, { unit: "px" }),
      vibrancy: bool(false),
      hue: int(0, 360, 220, { unit: "°" }),
      saturation: int(0, 100, 6, { unit: "%" }),
      borderGlow: int(0, 100, 0, { unit: "%" }),
      radius: int(0, 40, 24, { unit: "px" }),
      slant: int(0, 30, 14, { unit: "°" }),
      stars: int(0, 100, 0, { unit: "%" }),
      starSpeed: int(0, 100, 40, { unit: "%" }),
      nebula: int(0, 100, 0, { unit: "%" }),
      twinkle: bool(false),
      meteors: bool(false),
      textSize: int(11, 20, 13, { unit: "px" }),
      reduceMotion: bool(false),
    },
    layout: {
      dock: oneOf(DOCKS, "bottom"),
      width: int(40, 100, 100, { unit: "%" }),
      height: int(40, 100, 100, { unit: "%" }),
      autoHide: bool(true),
      hideDelay: int(200, 3000, 600, { unit: "ms" }),
      zone: int(1, 12, 2, { unit: "px" }),
      pinned: bool(false),
      display: text(24, "cursor", /^(cursor|id:\d{1,12})$/),
      margin: int(0, 40, 12, { unit: "px" }),
    },
    rainbow: {
      enabled: bool(true),
      screen: oneOf(["current", "all"], "current"),
      depth: int(30, 240, 90, { unit: "px" }),
      intensity: int(10, 100, 95, { unit: "%" }),
      speed: int(1, 10, 5),
      brightness: int(50, 150, 100, { unit: "%" }),
      thinking: bool(true),
      reactive: bool(true),
    },
    voice: {
      device: text(256, "", /^[\w\-:.# ]*$/),
      silence: int(150, 2000, 450, { unit: "ms" }),
      sensitivity: int(0, 100, 50, { unit: "%" }),
      mode: oneOf(["always", "ptt"], "always"),
      shortcut: text(48, "CommandOrControl+Shift+Space", ACCELERATOR),
      language: oneOf(["ko", "en", "auto"], "ko"),
    },
    // Emergency parity: global accelerators for the three always-visible buttons (retreat / hold / stop).
    emergency: {
      retreat: text(48, "CommandOrControl+Shift+R", ACCELERATOR),
      hold: text(48, "CommandOrControl+Shift+H", ACCELERATOR),
      stop: text(48, "CommandOrControl+Shift+X", ACCELERATOR),
    },
    server: {
      url: text(64, "http://127.0.0.1:8765"),
    },
    // Minimap route sketch (electron/sketch/*): hold Control+Option to draw on the SC2 minimap. fallbackShortcut toggles the same
    // sketch mode when the key helper has no Input Monitoring permission (press once to start drawing, again to send).
    sketch: {
      enabled: bool(true),
      fallbackShortcut: text(48, "Control+Alt+D", ACCELERATOR),
      captureImage: bool(true),
    },
    // BOARD: separate tactical board window (electron/board/*). anchor: upper-left | left edge; sizes are DIP.
    board: {
      enabled: bool(true),
      anchor: oneOf(["upper-left", "left"], "upper-left"),
      pageSize: int(3, 4, 3),
      width: int(300, 340, 320, { unit: "px" }),
      autoAdvanceSec: int(0, 30, 6, { unit: "s" }),
      offsetX: int(0, 200, 12, { unit: "px" }),
      offsetY: int(0, 300, 64, { unit: "px" }),
      hideWhenEmpty: bool(false),
      ageOutSec: int(0, 600, 45, { unit: "s" }),
      opacity: num(0.1, 1, 0.62, { decimals: 2, unit: "%", scale: 100 }),
      pageUpShortcut: text(48, "CommandOrControl+Shift+PageUp", ACCELERATOR),
      pageDownShortcut: text(48, "CommandOrControl+Shift+PageDown", ACCELERATOR),
    },
  });
  const GROUPS = Object.freeze(Object.keys(SCHEMA));

  function loopbackOrigin(value) {
    let url;
    try { url = new URL(value); } catch { return false; }
    return url.protocol === "http:" && ["127.0.0.1", "localhost", "[::1]"].includes(url.hostname) && !url.username && !url.password && url.pathname === "/" && !url.search && !url.hash;
  }

  function checkField(path, spec, value) {
    switch (spec.type) {
      case "num": {
        if (typeof value !== "number" || !Number.isFinite(value) || value < spec.min - 1e-9 || value > spec.max + 1e-9) throw new TypeError(`${path} must be ${spec.min}..${spec.max}`);
        if (spec.integer) return Math.round(value);
        const factor = 10 ** (spec.decimals === undefined ? 2 : spec.decimals);
        return Math.min(spec.max, Math.max(spec.min, Math.round(value * factor) / factor));
      }
      case "bool":
        if (typeof value !== "boolean") throw new TypeError(`${path} must be a boolean`);
        return value;
      case "enum":
        if (!spec.values.includes(value)) throw new TypeError(`${path} must be one of ${spec.values.join("|")}`);
        return value;
      case "str": {
        if (typeof value !== "string" || value.length > spec.max) throw new TypeError(`${path} must be a string up to ${spec.max} characters`);
        if (spec.pattern && !spec.pattern.test(value)) throw new TypeError(`${path} has an invalid format`);
        if (path === "server.url" && !loopbackOrigin(value)) throw new TypeError("server.url must be a loopback HTTP origin");
        return value;
      }
      default: throw new TypeError(`${path} has an unknown type`);
    }
  }

  function defaults(overrides = {}) {
    const out = {};
    for (const group of GROUPS) {
      out[group] = {};
      for (const [key, spec] of Object.entries(SCHEMA[group])) out[group][key] = spec.def;
    }
    if (overrides.serverUrl && loopbackOrigin(overrides.serverUrl)) out.server.url = new URL(overrides.serverUrl).origin;
    return out;
  }

  // Partial update {group:{key:value}}; unknown groups/keys and out-of-range values throw (never silently clamped).
  function validatePatch(patch) {
    if (!patch || typeof patch !== "object" || Array.isArray(patch)) throw new TypeError("settings patch must be an object");
    const out = {};
    let count = 0;
    for (const [group, values] of Object.entries(patch)) {
      if (!SCHEMA[group]) throw new TypeError(`unknown settings group ${group}`);
      if (!values || typeof values !== "object" || Array.isArray(values)) throw new TypeError(`settings.${group} must be an object`);
      out[group] = {};
      for (const [key, value] of Object.entries(values)) {
        const spec = SCHEMA[group][key];
        if (!spec) throw new TypeError(`unknown setting ${group}.${key}`);
        out[group][key] = checkField(`${group}.${key}`, spec, value);
        count += 1;
      }
    }
    if (!count) throw new TypeError("settings patch is empty");
    return out;
  }

  // Corrupt / old / hand-edited files never break the UI: bad fields fall back to defaults one by one.
  function sanitizeStored(raw, overrides) {
    const out = defaults(overrides);
    if (!raw || typeof raw !== "object") return out;
    for (const group of GROUPS) {
      const stored = raw[group];
      if (!stored || typeof stored !== "object") continue;
      for (const [key, spec] of Object.entries(SCHEMA[group])) {
        if (stored[key] === undefined) continue;
        try { out[group][key] = checkField(`${group}.${key}`, spec, stored[key]); } catch { /* keep default */ }
      }
    }
    return out;
  }

  function merge(current, patch) {
    const out = {};
    for (const group of GROUPS) out[group] = { ...current[group], ...(patch[group] || {}) };
    return out;
  }

  function nudgeAlpha(current, direction) {
    const spec = SCHEMA.glass.alpha;
    const next = Math.round((current + (direction > 0 ? 0.05 : -0.05)) * 100) / 100;
    return Math.min(spec.max, Math.max(spec.min, next));
  }

  const overlayView = settings => ({ ...settings.rainbow, loopSeconds: Math.round((30 / settings.rainbow.speed) * 10) / 10 });
  const voiceView = settings => ({
    silenceMs: settings.voice.silence,
    threshold: Math.round((0.9 - 0.8 * settings.voice.sensitivity / 100) * 100) / 100,
    ptt: settings.voice.mode === "ptt",
    language: settings.voice.language,
    shortcut: settings.voice.shortcut,
    device: settings.voice.device,
  });
  const resetPatch = () => defaults();

  return { SCHEMA, GROUPS, DOCKS, ACCELERATOR, defaults, validatePatch, sanitizeStored, merge, nudgeAlpha, overlayView, voiceView, resetPatch, loopbackOrigin };
});
