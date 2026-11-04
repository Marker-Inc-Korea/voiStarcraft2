# voiStarcraft2 Electron shell

An edge-docked, auto-hiding cosmic-glass command bar for the voice commander. There is **no window**:
the shell is a frameless, transparent, always-on-top (`screen-saver` level, all workspaces incl. fullscreen
apps) strip that slides in when the cursor touches the screen edge and is fully click-through otherwise, so
SC2 underneath keeps all mouse input. A second click-through window draws a rainbow glow along the monitor edge
while you are speaking.

Python Commander and SC2 lifecycles remain authoritative in the existing local HTTP service. The renderer has
**no network access** (CSP `connect-src 'none'`); everything goes through the allowlisted IPC below.

## Run

```
# 1. start the Commander web service (127.0.0.1:8765), with the voice backend (/api/realtime/*)
# 2. then:
cd electron && npm install && npm start        # VOI_COMMANDER_URL overrides the loopback origin
npm test                                       # node --test
```

Voice needs a Realtime-capable backend (`POST /api/realtime/session`, `POST /api/realtime/intent`). The ephemeral
secret is minted by the backend and only ever lives in the Electron **main** process. Bearer tokens for the
Commander service (`VOI_COMMANDER_TOKEN`) are also main-process only. macOS asks for microphone permission on
first use; only the microphone is ever granted.

## Dock behaviour

| | |
|---|---|
| Modes | `bottom` (default; ~min(980px, 90% width), grows upward) and `right` (full height, ~380px). Switch with the dock button; persisted. |
| Window | Sized once to the active mode's maximum footprint on the display holding the cursor; never resized. Show/hide is a CSS slide (`transform` + `opacity`, 220 ms). |
| Collapsed | Only a 3–4 px starlight handle shows; window is `setIgnoreMouseEvents(true, {forward:true})`, non-focusable. |
| Reveal | Main polls `screen.getCursorScreenPoint()` every 50 ms. Cursor in the last 2 px row (bottom) / column (right) across the bar span for 120 ms reveals it (dwell avoids SC2 edge-scroll false hits; shared multi-monitor edges never trigger). |
| Hide | Cursor outside panel + 12 px slop for 600 ms **and** 2.5 s since the last attention event. |
| Auto-reveal | While voice is listening / thinking / acting, on stage change, blocked, emergency, or error (`dock:attention`). Pin button keeps it open. |
| Shortcuts | `CommandOrControl+Shift+Space` toggles pin-open **and** voice. `CommandOrControl+Alt+Up/Down` nudge glass opacity ±5 %. |
| Focus | Hover never takes focus. Clicking the text input (or the shortcut) makes the window focusable and focuses it; collapsing blurs and sets `focusable:false`, so focus returns to the game. |
| Transparent areas | While revealed, the window is interactive only where the cursor is over the glass panel (cursor-poll hit test in main); the rest stays click-through. |

The reveal/hide decisions are the pure module `dock_state.js` (unit tested: edge hit, dwell, slop, debounce, voice
override, multi-display, mode switch).

### Glass transparency

The panel is *real* see-through glass: the window is `transparent` with `#00000000`; the fill is a low-alpha tint
(`--glass-alpha`, 8–90 %, default 28 %) and blur is `--glass-blur` (0–40 px, default 12). Both are live sliders in the
panel (no reload), persisted in `userData/dock-prefs.json`, validated in `protocol.js` (`validateGlass`). Text
shadow and card scrims strengthen automatically as alpha drops (`--legible`, `--scrim`). The starfield is drawn
additively on a transparent canvas, so it tints the glass without making it opaque.

CSS `backdrop-filter` only blurs content *inside* the window, never the desktop behind it. Real behind-window blur
is the optional macOS **vibrancy** toggle ("네이티브 블러", `setVibrancy('under-window')`), default off.

### Rainbow edge glow (`overlay/`)

A second frameless/transparent/click-through/non-focusable window (`screen-saver` level, visible over fullscreen
apps) covering the full bounds of the display that holds the dock (follows display changes). It renders a **feathered
glow**: eased linear-gradient masks (`mask-composite: add`, so corners blend seamlessly) cut a rotating conic
rainbow to full colour at the outermost pixel fading to transparent over `--edge-depth` (default 90 px), plus a
blurred wider bloom layer. The hue rotates by a compositor-only transform (6 s loop); loudness from the mic RMS
(`voice:level` → `overlay:level`, 0..1, smoothed) modulates intensity. Fades in/out in 150 ms. `thinking` is dimmer,
slower, breathing. `prefers-reduced-motion` → static. Shown only for `listening`/`thinking`.

## Execution stages (truthfulness)

Per command the renderer shows **Heard → Understood → Assigned → Ordered → Effect observed** with a tier badge
(macro / operation / micro / emergency); parallel operations are separate cards each with their own rail.
`renderer/stages.js` derives stages only from backend evidence (`/api/micromachine/status` operations →
`intervention.command_execution.stages`, `disposition`, `consumption_status`, `command_queue`, `latest_request`).
A stage is "done" only when the backend reports it; **Effect observed appears only for `effect_observed`**. Blocked
reasons (`blocker_reason`, `refusal_reason`, failed stage reason, send errors such as `direct_control_conflict`)
are shown verbatim.

`direct_runtime` (from `/api/realtime/intent` and `/api/micromachine/status`): for non-macro tiers,
`kind: "dry_run_fake_bot"` renders Ordered/Effect as dashed **"시뮬레이션(가짜 봇)"** and never as real; a missing field
shows a muted "실행 대상 미확인" badge and nothing past Understood is claimed. Macro vectors are MicroMachine.

## IPC / voice contract

Renderer API (`window.commander`, from `preload.js`): `status() runtimeStatus() capabilities() registry()
registerTargetPin(pin) events(after) sendCommand(text, lang) platform`, `onEvent(cb)`, `voice.{start, stop, state,
onState, sendAudio, sendLevel, micError}`, `dock.{state, setMode, setPinned, setGlass, focus, reportRect, attention}`.

| Channel | Dir | Payload / validation (`protocol.js`) |
|---|---|---|
| `voice:start` / `voice:stop` / `voice:state` | invoke | `{}`; returns `{state, active, reason, turn}` |
| `voice:audio` | send | binary pcm16 24 kHz mono, 2..48000 bytes, even length (`validateVoiceAudio`) |
| `voice:level` | send | number 0..1 |
| `voice:mic-error` | send | `{reason}` ≤160 chars |
| `dock:mode` / `dock:pin` / `dock:glass` / `dock:focus` | invoke | `{mode: bottom\|right}` / `{pinned}` / `{alpha 0.08..0.9, blur 0..40, vibrancy}` / `{focus}` |
| `dock:rect` / `dock:attention` | send | window-relative panel rect / `{reason: stage\|blocked\|emergency\|error}` |
| `commander:push` | main→renderer | `{type:"voice-state"\|"transcript"\|"intent"\|"dock-state", ...}` |
| `overlay:state` / `overlay:level` | main→overlay | `{state: off\|listening\|thinking}` / 0..1 |

Voice states: `idle | connecting | listening | thinking | acting | error` (`active` says whether voice is armed).
`voice.js` (Node `ws`):

1. `POST /api/realtime/session` → `{client_secret, expires_at, ws_url, model, tools}`; connect with
   `Authorization: Bearer <secret>` and `User-Agent: curl/8.7.1` (the Cloudflare-fronted proxy blocks the default UA).
2. `session.update`: PCM 24 kHz, `server_vad` (`create_response:true`), input transcription (`VOI_TRANSCRIBE_MODEL`,
   `off` disables; `VOI_VAD_SILENCE_MS` 150–2000, default 450).
3. Mic chunks → `input_audio_buffer.append`. `speech_started` → listening, `speech_stopped` → thinking,
   `conversation.item.input_audio_transcription.*` → live transcript, `response.function_call_arguments.done`
   (`submit_command`) → `POST /api/realtime/intent {tool_arguments, transcript, latency_ms}` → acting → idle.
   Processed conversation items are deleted so each utterance is interpreted independently.
4. `latency_ms`: `speech_end_to_tool_call`, `speech_ms`, `transcript_wait`; `tool_call_to_publish` is measured around the
   intent POST and shown on the card.
5. Secrets are re-minted when within 30 s of expiry or after auth failures; the socket reconnects with back-off
   (0.5→8 s) and the session rotates after 55 min when quiet. Errors surface as `error` state with the reason.

Permissions: only `media` (audio) for the dock window; everything else, including device APIs, is denied.
The state machine is the pure module `voice_state.js`.

## Tests and smoke

`npm test` (65 tests): IPC validators, dock reveal logic, voice state machine + client against a fake `ws`, stage
derivation, window/overlay flags, glass limits.

Smoke (real Electron, fake backend; **not** SC2 evidence):

```
swiftc -O smoke/warp_cursor.swift -o /tmp/warp_cursor    # real cursor moves via CoreGraphics
npx electron smoke/driver.js                              # dock/hover/focus/voice/overlay/glass checks + docs/screens/*.png
npx electron smoke/overlay_shots.js                       # edge glow over bright/dark backdrop (screencapture -x)
node fixtures/fake_backend.js --runtime dry_run_fake_bot|live|missing   # serve the fake backend by hand
```

Screenshots are in `docs/screens/`; `smoke-report.json` lists each check. The fake Chromium microphone is used
(`--use-fake-device-for-media-stream`), so the real-speech path is untested here.
