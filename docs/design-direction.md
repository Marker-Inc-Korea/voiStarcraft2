# Design direction

Unslop pass (method: [unslop](https://github.com/heydamir/unslop) by Damir Sokolovsky, CC BY 4.0).

- **Product.** A voice and minimap-sketch commander that sits over StarCraft II, so a player gives orders without leaving the game.
- **Character.** Terse, tactical, steady.
- **World.** A night-shift operations console: instruments over a live map. Surfaces are graphite glass; nothing competes with the game.
- **Moments.** Two places get colour: the listening edge (the rainbow overlay, a live state) and the route line on the minimap sketch.
- **Signature.** The route line: one console-cyan stroke (`--accent`) for a drawn order. The same accent marks the selection and the live operation count, nowhere else.

## Rules for every surface

- Tokens: `electron/renderer/tokens.css`. One accent (`--accent`, text on it `--accent-ink`), one easing (`--ease`), weights 400/500/600.
- Surfaces neutral: the glass hue/saturation settings still exist, but default to graphite (saturation low).
- No colored glows, no radial colour washes, no hue-to-hue gradients in the chrome, no looping motion on things that are not live.
- Motion that explains a live state stays (listening, connecting, thinking). Idle things are static.
- Decorative layers (stars, nebula, meteors, border bloom) remain as settings, off by default.
- Copy: short Korean words, no trailing periods on labels, no exclamation marks, no emoji.
