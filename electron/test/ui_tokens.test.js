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
  assert.match(tokens, /cubic-bezier\(\.4, 0, \.2, 1\)/); assert.match(tokens, /cubic-bezier\(0, 0, \.2, 1\)/);
  assert.match(tokens, /--fg-l: 98\.5%/); assert.match(tokens, /prefers-color-scheme: light[\s\S]*--fg-l: 14\.5%/);
  for (const alpha of [".85", ".55", ".15", ".10"]) assert.ok(tokens.includes(`/ ${alpha})`), alpha);
  assert.match(tokens, /--border: oklch\(var\(--fg-l\)[^;]*\/ \.10\)/);
  assert.match(tokens, /system-ui/); assert.doesNotMatch(tokens, /AsideDisplay|Geist"?,\s*-apple/);
  for (const weight of [400, 500, 600, 700]) assert.match(tokens, new RegExp(`--fw-\\w+: ${weight}`));
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
  for (const selector of [".em {", ".gear {", ".cmd-form input {", ".btn {", ".switch {", ".tabs button {", ".transcript {", ".ops {"]) {
    const rule = hud.slice(hud.indexOf(selector), hud.indexOf("}", hud.indexOf(selector)));
    assert.match(rule, /border-radius: (var\(--radius-(md|lg|xl|pill)\)|50%)/, selector);
  }
  for (const selector of [".card {", ".tier {", ".badge {", ".label {"]) {
    const rule = board.slice(board.indexOf(selector), board.indexOf("}", board.indexOf(selector)));
    assert.match(rule, /border-radius: var\(--radius-(xl|pill)\)/, selector);
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

test("ui_fx.fillPercent: slider fill maths", () => {
  assert.equal(Fx.fillPercent(0, 100, 50), 50); assert.equal(Fx.fillPercent(8, 90, 8), 0); assert.equal(Fx.fillPercent(8, 90, 90), 100);
  assert.equal(Fx.fillPercent(0, 1, 0.28), 28); assert.equal(Fx.fillPercent(0, 3, 1), 33.3);
  assert.equal(Fx.fillPercent(0, 100, 150), 100); assert.equal(Fx.fillPercent(0, 100, -5), 0);
  assert.equal(Fx.fillPercent(5, 5, 5), 0); assert.equal(Fx.fillPercent("a", 5, 5), 0); assert.equal(Fx.fillPercent(0, 10, NaN), 0);
});
