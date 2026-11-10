# Second cursor (virtual cursor)

The user's real mouse belongs to the user. Nothing the agent does needs it:

| What acts | How | Real mouse |
|---|---|---|
| Game orders (voice, text, route sketch) | SC2 API through Direct / MicroMachine | never used |
| Agent ghost cursor on the minimap | DOM element in the sketch window | never moved |
| Our own QA (`electron/smoke/driver.js`, `e2e_real.js`, `overlay_shots.js`) | second cursor, below | never moved |
| Human-input probes (`focus_probe.js`, `sketch_probe.js`) | real click / drag, **opt-in** `REAL_POINTER=1` | moved |

## How the second cursor works

Same approach as HeyClicky (`/Applications/HeyClicky.app`): a drawn cursor plus Accessibility actions, not synthetic mouse
events. Its binary imports `AXUIElementPerformAction` / `AXUIElementSetAttributeValue` and draws its own cursor overlay.

- `electron/smoke/ax_cursor.swift` (`/tmp/voi_ax_cursor serve PID`): sets `AXManualAccessibility` on the Electron app so Chromium
  builds its accessibility tree, finds elements by `AXDOMIdentifier` (= DOM `id`), and runs `press` (`AXPress`), `focus`
  (`AXFocused`), `set` (`AXValue`) or `frame` (`AXPosition`/`AXSize`). JSON lines over stdin/stdout.
- `electron/smoke/vcursor.js`: starts the helper, glides a click-through, non-focusable pink pointer window (one level above
  the HUD) to the element centre, then acts. Hover-only behaviour (dock reveal) uses main's `_internals.setFakeCursor`.
- Guard: `electron/test/vcursor.test.js` fails if either file creates, posts or taps input events or moves the cursor.

## Measured on this Mac (macOS 15.6.1, Electron 36.9.5)

| Path to a background window | Result |
|---|---|
| Per-process event posting (window-number fields set, any event source) | nothing delivered; HID / combined source moved the real cursor |
| Private SkyLight focus-without-raise + post | status 0, nothing delivered |
| Accessibility `AXPress` / `AXFocused` + `AXValue` | delivered; cursor unmoved; frontmost app unchanged |

`smoke/driver.js` in default mode: 64/64 checks, including "second cursor never made this app frontmost" (frontmost sampled
every 250 ms) and "typing does not take OS focus". A separate cursor sampler (100 ms) saw the real pointer only where the
user moved it, never on the display under test.

## Limits

- Needs Accessibility permission for the process that launches the helper (a fresh Mac asks once).
- Accessibility typing sets the value; it does not produce key events. Keyboard behaviour is covered by `focus_probe.js` (opt-in).
- SC2 itself is not driven through Accessibility; game orders use the API.
