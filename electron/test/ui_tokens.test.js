const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const Fx = require("../renderer/ui_fx.js");

const dir = path.join(__dirname, "..");
const read = (...parts) => fs.readFileSync(path.join(dir, ...parts), "utf8");
const SURFACES = { "renderer/style.css": read("renderer", "style.css"), "board/board.css": read("board", "board.css"), "overlay/overlay.css": read("overlay", "overlay.css") };
const tokens = read("renderer", "tokens.css");
const stripComments = css => css.replace(/\/\*[\s\S]*?\*\//g, "");

test("tokens.css: Aside radius scale (x1.4 squircle), 4px spacing, blur steps, easings, alpha ladder, light + dark", () => {
  assert.match(tokens, /--squircle: 1\.4/);
  for (const [name, rem] of [["md", ".375rem"], ["lg", ".5rem"], ["xl", ".75rem"], ["2xl", "1rem"], ["3xl", "1.5rem"]]) assert.match(tokens, new RegExp(`--radius-${name}: calc\\(${rem.replace(".", "\\.")} \\* var\\(--squircle\\)\\)`));
  assert.match(tokens, /--radius-pill: 1000px/);
  assert.match(tokens, /--space-1: \.25rem/);
  for (const px of [4, 8, 12, 16, 24, 64]) assert.match(tokens, new RegExp(`--blur-\\w+: ${px}px`));
  assert.match(tokens, /--ease: cubic-bezier\(/); assert.doesNotMatch(tokens, /--ease-(in-out|out)\b/);
  assert.match(tokens, /--fg-l: 98\.5%/); assert.match(tokens, /prefers-color-scheme: light[\s\S]*--fg-l: 14\.5%/);
  for (const alpha of [".85", ".55", ".15", ".10"]) assert.ok(tokens.includes(`/ ${alpha})`), alpha);
  assert.match(tokens, /--border: oklch\(var\(--fg-l\)[^;]*\/ \.10\)/);
  assert.match(tokens, /system-ui/); assert.doesNotMatch(tokens, /AsideDisplay|Geist"?,\s*-apple/);
  for (const weight of [400, 500, 600]) assert.match(tokens, new RegExp(`--fw-\\w+: ${weight}`));
  assert.doesNotMatch(tokens, /--fw-bold|--fw-\w+: [789]00/);
});

test("no angular regressions: no clip-path polygons, no square corners on surfaces", () => {
  for (const [file, raw] of Object.entries(SURFACES)) {
    const css = stripComments(raw);
    assert.doesNotMatch(css, /clip-path\s*:\s*polygon/, `${file}: clip-path polygon (HUD silhouette is applied from JS by hud_shape.js)`);
    assert.doesNotMatch(css, /border-radius\s*:\s*0(?![.\d])/, `${file}: border-radius: 0`);
    assert.doesNotMatch(css, /outline-brackets|chamfer/i, `${file}: angular chamfer/bracket leftovers`);
  }
  const hud = stripComments(SURFACES["renderer/style.css"]);
  assert.doesNotMatch(hud, /\.panel\s*\{[^}]*(border-radius|clip-path)/, ".panel shape belongs to hud_shape.js");
});

test("every border-radius is a token, a circle, or a >= 6px value", () => {
  for (const [file, raw] of Object.entries(SURFACES)) {
    for (const [, value] of stripComments(raw).matchAll(/border-radius\s*:\s*([^;}]+)/g)) {
      for (const part of value.trim().split(/\s+/)) {
        if (/^var\(--radius-(xs|sm|md|lg|xl|2xl|3xl|pill)\)$/.test(part) || part === "50%" || part === "var(--radius)") continue;
        const px = /^(\d+(?:\.\d+)?)px$/.exec(part);
        assert.ok(px && Number(px[1]) >= 6, `${file}: border-radius ${part}`);
      }
    }
  }
});

test("interactive surfaces (buttons, inputs, cards, rows, chips) use rounded tokens, never a bare small px radius", () => {
  const hud = stripComments(SURFACES["renderer/style.css"]), board = stripComments(SURFACES["board/board.css"]);
  for (const selector of [".gear {", ".cmd-form input {", ".btn {", ".switch {", ".tabs button {", ".transcript {", ".ops {"]) {
    const rule = hud.slice(hud.indexOf(selector), hud.indexOf("}", hud.indexOf(selector)));
    assert.match(rule, /border-radius: (var\(--radius-(md|lg|xl|pill)\)|50%)/, selector);
  }
  for (const selector of [".card {", ".badge {"]) {
    const rule = board.slice(board.indexOf(selector), board.indexOf("}", board.indexOf(selector)));
    assert.match(rule, /border-radius: var\(--radius-(md|xl|pill)\)/, selector);
  }
});

test("both skins and both appearances: terminal keeps phosphor monospace as colour, light mode exists, reduced motion respected", () => {
  const hud = stripComments(SURFACES["renderer/style.css"]), board = stripComments(SURFACES["board/board.css"]);
  assert.match(hud, /body\[data-skin="terminal"\][\s\S]*--term: #2cff7a/); assert.match(hud, /@media \(prefers-color-scheme: light\)/);
  assert.match(board, /body\[data-skin="terminal"\][\s\S]*#00ff66/); assert.match(board, /@media \(prefers-reduced-motion: reduce\)/);
  assert.match(hud, /@media \(prefers-reduced-motion: reduce\)/);
});

test("HUD and board link the shared token sheet; stylesheet-only CSP stays valid (no inline style, no remote font)", () => {
  assert.match(read("renderer", "index.html"), /href="\.\/tokens\.css"/); assert.match(read("board", "index.html"), /href="\.\.\/renderer\/tokens\.css"/);
  for (const css of [tokens, ...Object.values(SURFACES)]) assert.doesNotMatch(css, /@import|url\(\s*["']?https?:/);
});

test("type: tabular numerals for counters, latency, values and card status", () => {
  const hud = stripComments(SURFACES["renderer/style.css"]), board = stripComments(SURFACES["board/board.css"]);
  assert.match(hud, /\.ctl output \{[^}]*tabular-nums/); assert.match(hud, /\.ops \{[^}]*tabular-nums/); assert.match(board, /\.status \{[^}]*tabular-nums/); assert.match(board, /\.label \{[^}]*tabular-nums/);
});

test("HUD stylesheet: quiet instrument. No glows, one easing, named transitions, no idle loops, no animated width", () => {
  const hud = stripComments(SURFACES["renderer/style.css"]);
  assert.doesNotMatch(hud, /--ease-(in-out|out)|--fw-bold|font-weight: *[789]00/);
  assert.doesNotMatch(hud, /transition: *all|transition-property: *all/);
  for (const [, value] of hud.matchAll(/transition: *([^;}]+)/g)) for (const part of value.split(/,(?![^(]*\))/)) assert.match(part.trim(), /^[a-z-]+ .*var\(--(ease|slide|dur[a-z-]*)\)|^[a-z-]+ \d+ms var\(--ease\)/, `transition ${part.trim()}`);
  assert.doesNotMatch(hud, /transition[^;]*\bwidth\b/, "no animated width (the level meter scales with transform)");
  // glow = a box-shadow / text-shadow / filter blur with a colour that is not a neutral shadow token
  for (const [, value] of hud.matchAll(/box-shadow\s*:\s*([^;}]+)/g)) for (const layer of value.split(/,(?![^(]*\))/)) { // a layer with a blur radius must be a neutral shadow, never a colour
    const lengths = layer.trim().replace(/^inset\s+/, "").split(/\s+/).filter(t => /^-?[\d.]+(px)?$/.test(t));
    if (lengths.length >= 3 && parseFloat(lengths[2]) > 0) assert.match(layer, /rgb\(var\(--shadow-rgb\)|rgb\(0 0 0/, `box-shadow glow ${layer.trim()}`);
  }
  assert.doesNotMatch(hud, /(?<!backdrop-)filter: *blur|radial-gradient/, "no blurred bloom or radial colour wash in the chrome");
  assert.doesNotMatch(hud, /@keyframes (breathe|orbit|sweep|flicker|scan)/);
  // looping animation only on live voice states (connecting, listening, thinking), the blinking terminal cursor/LEDs and nothing idle
  for (const [, selector] of hud.matchAll(/([^{}]+)\{[^{}]*animation: *(?:spin|blink)\b[^{}]*infinite/g)) assert.match(selector, /data-voice="(connecting|listening|thinking)"|\.cursor/, `looping animation on ${selector.trim()}`);
});
test("HUD markup has no planet/sheen/sweep layers; the border setting draws a neutral hairline, not a bloom", () => {
  const html = read("renderer", "index.html"), hud = stripComments(SURFACES["renderer/style.css"]);
  assert.doesNotMatch(html, /orb-ring|class="moon"|class="sheen"|class="sweep"|outline-glow/);
  assert.match(html, /id="outline-edge"/); assert.match(hud, /\.outline-edge \{[^}]*stroke: var\(--border-strong\)/);
  assert.doesNotMatch(hud, /hsl\(var\(--hue\)[^)]*\/ *\.\d+\)/, "no hue-driven colour washes in the chrome");
});

test("ui_fx.fillPercent: slider fill maths", () => {
  assert.equal(Fx.fillPercent(0, 100, 50), 50); assert.equal(Fx.fillPercent(8, 90, 8), 0); assert.equal(Fx.fillPercent(8, 90, 90), 100);
  assert.equal(Fx.fillPercent(0, 1, 0.28), 28); assert.equal(Fx.fillPercent(0, 3, 1), 33.3);
  assert.equal(Fx.fillPercent(0, 100, 150), 100); assert.equal(Fx.fillPercent(0, 100, -5), 0);
  assert.equal(Fx.fillPercent(5, 5, 5), 0); assert.equal(Fx.fillPercent("a", 5, 5), 0); assert.equal(Fx.fillPercent(0, 10, NaN), 0);
});
