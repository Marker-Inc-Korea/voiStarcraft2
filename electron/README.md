# voiStarcraft2 Electron shell

Edge-docked, auto-hiding glass command bar. No window chrome: a frameless, transparent, always-on-top
(`screen-saver` level, all workspaces incl. fullscreen apps) strip that slides in when the cursor touches a screen
edge and is click-through otherwise, so SC2 keeps all mouse input. A second click-through window draws the rainbow
edge glow while you speak. The renderer has no network access (CSP `connect-src 'none'`); everything goes through IPC.

```
# start the Commander service on 127.0.0.1:8765 (with /api/realtime/*), then
cd electron && npm install && npm start      # VOI_COMMANDER_URL overrides the loopback origin
npm test                                     # node --test
```

## HUD

A bottom-anchored control HUD: a slanted bar (wide base on the screen edge, narrower far edge; slant 0-30°, default 14°, 유리 > 기울기)
with **all four corners rounded** (continuous-curvature "squircle" Bézier corners; far corners use `glass.radius`, the two base corners
on the screen edge use 0.6 x that). Left/right/top docks are plain **rounded rectangles** (no slant, same radius token).
`renderer/hud_shape.js` is the single pure geometry: it produces the CSS `clip-path: path()`, the SVG outline path for the rim/glow
and a densely sampled polygon (24 points per corner) that main uses for pointer hit-testing, so the rounded corners, the slanted
sides and everything outside the shape stay click-through to the game (`main.js` `pointerOverPanel`, not the rectangle). Radii are
clamped so they never overlap (a radius larger than half the height becomes a pill). Words only: orb · one-line transcript · `작전 N` (count of OPEN
operations: it falls as they complete or block; only when none is open does it show the session total) · input (`명령`) · gear (with the status LED). **There are no retreat/hold/stop buttons in the HUD** (removed by user decision; the emergency path below stays). **There are no operation cards in the HUD**: the renderer still derives
them from backend evidence (`stages.js` + `board_cards.js`: heard/understood/assigned/ordered/effect; 효과 only for
`effect_observed`, `시뮬` for the fake bot) and publishes them through `window.commander.board.publish(cards)` -> `board:publish`
to the tactical board window (`electron/board*`, separate small translucent window, upper-left).

Gear (or `Cmd/Ctrl+,`) opens settings in the same glass, instantly; Esc / gear / click outside closes it; the HUD stays
pinned open meanwhile. Tabs: 유리 레이아웃 무지개 음성 긴급 (전술판) 서버 초기화. Settings apply live, persist in
`userData/settings.json`, and are schema-validated (`renderer/settings.js`, range errors throw, never clamp).

### Emergency parity

Emergency has no HUD buttons. It is reached by the voice fast rules (`후퇴`/`홀드`/`멈춰`), `Cmd/Ctrl+.` (retreat) in the HUD, and the
global shortcuts, which take the **same route**: shortcut -> main -> `runEmergency` -> `POST /api/emergency {action}` (deterministic, no
model; enum `retreat|hold|stop`, validated in `protocol.js`, also exposed as `window.commander.emergency(action)` -> IPC
`commander:emergency`) -> one emergency card to the board. Defaults `Cmd/Ctrl+Shift+R` retreat, `+H` hold, `+X` stop (설정 > 긴급). A shortcut
that cannot be registered shows `키불가` in settings; it is never silent. Registration logic is pure and tested
(`shortcuts.js`, fake `globalShortcut`).

### Skins (유리 > 스킨)

`cosmic` (우주): graphite glass (the default look; the starfield/nebula below are opt-in sliders, off by default). `terminal` (터미널): StarCraft
in-game computer console: near-black translucent panel, phosphor green (`#2cff7a`) monospace (`SF Mono`/Menlo/IBM Plex Mono),
static scanlines, a block cursor (`> 명령_`) that blinks only while the empty input has focus, and green status LEDs that blink only while
listening/thinking; geometry is rounded like the cosmic skin. Both skins share opacity/blur/hue settings, reduced-motion support and the rainbow
edge overlay. The terminal text scrim is computed from the opacity (`renderer/skins.js`) so it stays legible at 8 % opacity. The switch is
live (no reload) and persisted (`glass.skin`).

## Minimap route sketch (hold Control+Option, draw on the SC2 minimap)

While **Control+Option (⌃⌥, nothing else)** is held, a transparent non-activating panel appears exactly over the calibrated SC2 minimap and takes
the user's own mouse; everywhere else stays click-through and the game keeps keyboard focus. Releasing the chord ends the sketch. Esc, a click without
a drag, another key, the helper dying, or disabling the feature **cancels** (nothing is sent, the HUD line says so). On release with >= 1 stroke:
payload (`update_id`, `strokes` [[nx,ny,t_ms]] normalized to the minimap FRAME, `image_png_b64` crop <= 512 px or `null`, `transcript` of the last 10 s,
`minimap_rect`) -> `POST /api/route/sketch` through main's `requestJson` -> the answer (`narration_ko` / `question_ko`) is the HUD transcript line; the
strokes stay visible, faded, until a `route_plan` arrives (then the planned route replaces them). An `execute` answer without a route is shown as
`invalid`, never as executing.

- **Chord detection**: `native/chord_helper.swift` (listen-only `CGEventTap`, never posts events, never touches the cursor; build with
  `sh electron/native/build.sh` -> `native/bin/chord_helper`) writes `{"chord":"down"|"up"}`, `{"key":"other"}`, `{"status":"ready"|"denied"}` lines.
  `sketch/helper.js` supervises it (restart with backoff, crash-loop stop, slow retry while denied, "lost" -> cancel); `sketch/chord.js` is the pure state
  machine (120 ms debounce, interruption by another key, toggle takeover). Needs **Input Monitoring**; without it (or without the build) settings > 미니맵
  explains in Korean and the fallback toggle `sketch.fallbackShortcut` (default `Control+Alt+D`, press = start, press again = send, Esc = cancel) works.
- **Native mouse capture (keeps the game's focus)**: a mouse-down on any Electron window activates our app and steals SC2's keyboard focus (Electron 36's
  `type:"panel"` is not a real non-activating NSPanel). So while sketching, main sends the helper `{"rect":{x,y,w,h}}` + `{"capture":true}` (stdin, one JSON
  per line; also for the fallback toggle) and the helper runs an **active** tap on left mouse down/drag/up only: a press that starts inside the minimap rect is
  reported (`{"mouse":"down"|"drag"|"up","x","y","t"}`) and **consumed** (neither SC2 nor our windows see it); everything outside the rect passes through untouched.
  It never posts events or moves the cursor. The sketch window stays click-through and renders strokes built from those messages (`sketch/native_capture.js`).
  Needs **Accessibility**; without it (or without the helper) the Electron panel takes the mouse for that sketch and settings > 미니맵 > 손쉬운 사용 says
  "손쉬운 사용 권한이 없어 그리는 동안 게임 포커스가 잠깐 넘어갈 수 있습니다" with how to grant it.
- **Screen capture**: `desktopCapturer` + Screen Recording permission. Without it the image is `null` and settings says so; a black capture is never sent.
- **Minimap position** (assumption, live SC2 unverified): default guess = square at the bottom-left of the display, side 24.5 % of the display height,
  1 % margin (`sketch/geometry.js`). 설정 > 미니맵 > 위치 맞추기 opens a drag/resize overlay; the rect is stored per display id + resolution + scale factor
  (`userData/minimap.json`). Strokes are normalized to the minimap FRAME only; the letterboxed playable area is the backend's job.
- **Route display + agent cursor**: route events come from `GET /api/route/events?after=<route_seq>` (`sketch/feed.js`): `route_plan` -> dashed polyline,
  numbered waypoints, sword (attack) / flag (move) at the end; `route_progress` -> reached markers (final point reached only on `done:true`), fade on
  `done`/`released`; `agent_cursor` -> a ghost cursor (distinct shape/colour, `label_ko` tooltip) gliding to each point. It is a DOM element in the sketch window; **no code
  in the app moves, warps or posts to the real pointer** (`test/sketch_no_cursor_warp.test.js` scans every shipped source; only `electron/smoke` may).
- **HUD**: the route is one more operation card on the tactical board (`sketch/route_model.js` `card()`; stages move only on backend events; 시뮬 for the fake bot)
  whose name is the progress text `앞마당 ✓ → 맵 중앙 (이동 중) → 적 3번째 → 공격`.
- **Smoke**: `npx electron smoke/route_shots.js [outDir]` headless render of both skins (no pointer). `npx electron smoke/sketch_probe.js` is the supervisor's
  real-pointer probe (simulated chord -> real helper -> real OS drag; a downstream witness tap proves inside presses are consumed and outside clicks pass; the frontmost app is sampled every 100 ms); it refuses to run when `CGSSessionScreenIsLocked` is 1 (two S), the console is locked or the capture is dark.

## Design language

Direction and rules: `docs/design-direction.md` (an unslop pass, method by [heydamir/unslop](https://github.com/heydamir/unslop), CC BY 4.0).
`renderer/tokens.css` is the single token layer shared by the HUD, the tactical board, the sketch window and the overlay: corner scale with a
1.4 squircle factor, 4 px spacing unit, blur steps 4/8/12/16/24/64, ONE easing (`--ease`), weights 400/500/600, a neutral oklch palette with
the .85/.55/.15/.10 alpha ladder (dark default; light via `prefers-color-scheme`), one fixed accent (`--accent`, console cyan, independent of the
glass hue) used for the route line, the selection and the live count only, and a hairline material edge. No coloured glows, no colour washes, no
looping motion on things that are not live. The HUD silhouette (clip-path) belongs to `hud_shape.js`. `test/ui_tokens.test.js`,
`test/board_view.test.js` and `test/sketch_overlay_style.test.js` guard against square corners, glows and idle loops creeping back.
The system font stack (SF on macOS) is used; no font files are vendored.

## Cosmic glass (skin 우주)

Graphite glass by default (색조 220°, 채도 6 %). Optional decoration on one transparent canvas (`renderer/space.js`, pure model `renderer/sky.js`):
a parallax starfield (별 / 별 속도 / 반짝임 / 유성) and nebula clouds (성운). All of them default to off; with 별 and 성운 at 0 the canvas is
cleared once and no animation loop runs. When on: one rAF loop capped at ~30 fps, paused while the bar is collapsed, a single still frame under
reduced motion. The mic is a flat round control; its ring shows the voice state (rainbow while listening, an accent arc while thinking).

## Multi-display

Display choice and all geometry live in the pure `displays.js` (negative origins, portrait, mixed scale factors): the
dock follows the display under the cursor (250 ms dwell, collapsed only) unless 모니터 is fixed; windows are flush with
`display.bounds`; 무지개 > 화면 `현재 | 전체` (one overlay window per display). Smoke: `SMOKE_DISPLAY=primary|<index>|id:<n>`.

## Dock behaviour

- Positions `bottom | right | left | top`, size scale, margin, display (follow cursor or fixed). Window is sized once to
  the dock's maximum footprint; show/hide is a CSS slide, never a resize.
- Main polls the cursor every 50 ms (`dock_state.js`, pure + tested). Reveal: cursor in the edge zone for 120 ms
  (shared multi-monitor edges never trigger). Hide: outside panel + 12 px for the hide delay and 2.5 s after the last
  attention event. Voice listening/thinking/acting, stage change, blocked, emergency and error force it open.
- `Cmd/Ctrl+Shift+Space` toggles pin-open + voice (push-to-talk mode: press = start, press = send).
  `Cmd/Ctrl+Alt+Up/Down` nudge opacity ±5 %.
- Focus: hover never focuses. Clicking the input (or the shortcut) focuses; collapsing blurs and sets `focusable:false`.
- Glass is real see-through (low-alpha tint). CSS `backdrop-filter` only blurs inside the window; the optional macOS
  **vibrancy** toggle (`네이티브 블러`) gives behind-window blur.

## Rainbow edge glow (`overlay/`)

Feathered vignette (not a stroke): strongest at the display edge, fading to transparent over `depth` px; rotating
conic hue (compositor-only), mic-level reactive, dimmer slow breathing for `thinking`, 150 ms fade, reduced-motion
aware. Reads its own settings subset (`rainbow.*`) pushed over `overlay:settings`.

## IPC / voice contract

`window.commander`: `status runtimeStatus capabilities registry registerTargetPin events sendCommand emergency board.publish`,
`onEvent(cb)` (`voice-state | transcript | intent | dock-state | settings | session | emergency`), `voice.{start stop state sendAudio
sendLevel ptt micError}`, `dock.{state focus reportRect attention}`, `settings.{get set reset open probeServer
checkShortcut previewRainbow}`. All payloads are validated in `protocol.js`.

Voice states: `idle | connecting | listening | thinking | acting | error`. `voice.js` (Node `ws`):
`POST /api/realtime/session` → connect `ws_url` with `Authorization: Bearer <secret>` + `User-Agent: curl/8.7.1` →
`session.update` (PCM 24 kHz, `server_vad`, transcription; VAD silence/sensitivity/language/PTT from settings) → mic
chunks as `input_audio_buffer.append` → `speech_started` = listening, `speech_stopped` = thinking, `submit_command`
tool call → `POST /api/realtime/intent {tool_arguments, transcript, latency_ms}` = acting → idle. Latency
(`speech_end_to_tool_call`, `tool_call_to_publish`) is measured per turn (last 10 in 음성). Secret re-mint before
expiry, back-off reconnect, session rotation after 55 min. Only the microphone permission is ever granted.

## Verification

`npm test` runs the pure unit tests (validators, rounded HUD geometry + hit-test, skins, emergency shortcut registry, board card view-model, settings schema, dock and multi-display logic, voice state machine
and client on a fake `ws`, stage derivation, window flags). They never launch Electron or touch the pointer.

The real-Electron smoke runs use a **second cursor** by default (`smoke/vcursor.js` + `smoke/ax_cursor.swift`, see
`docs/virtual-cursor.md`): hover positions go through main's fake-cursor hook, clicks and typing through the macOS
Accessibility API on our own windows, and a pink pointer window shows where it acts. Your real mouse, keyboard and
frontmost app are left alone (the driver checks the frontmost app every 250 ms). Needs Accessibility permission for the
terminal that launches it. Screenshots go to a temp dir unless `SMOKE_SAVE_SHOTS=1`; saved captures show your desktop
and are git-ignored (`docs/screens/`, `docs/e2e/*.png`).

```
SMOKE_DISPLAY=primary npx electron smoke/driver.js      # also: SMOKE_DISPLAY=1, SMOKE_DISPLAY=2
npx electron smoke/overlay_shots.js

# real end-to-end: real web_gui --dry-run + real Realtime proxy + TTS wav as the Chromium mic (docs/e2e/RESULTS.md)
E2E_NAME=u1_scout_attack VOI_COMMANDER_URL=http://127.0.0.1:18765 npx electron smoke/e2e_real.js

# opt-in, takes over your REAL mouse (only to test a human click / drag): run when the Mac is otherwise idle
swiftc -O smoke/warp_cursor.swift -o /tmp/warp_cursor; swiftc -O smoke/cursor_daemon.swift -o /tmp/cursor_daemon
REAL_POINTER=1 SMOKE_DISPLAY=primary npx electron smoke/driver.js
REAL_POINTER=1 npx electron smoke/focus_probe.js        # real click + real keys into the input
REAL_POINTER=1 npx electron smoke/sketch_probe.js       # real drag across the minimap
```

Not verified: real microphone speech, real SC2, the overlay over macOS fullscreen apps, canvas CPU cost.

## Tactical board (separate window)

Operation cards do not live in the HUD. The HUD renderer publishes its derived card list (`commander.board.publish(cards)` -> `board:publish`);
main validates it (`protocol.validateBoardCards`: <= 24 cards, 5 stage states each) and `electron/board.js` owns a small translucent,
frameless, non-focusable, always-on-top window (`electron/board/*`) on the display the HUD is on.

- Geometry (`board/layout.js`): 300-340 px wide, upper-left (`board.anchor` = `upper-left` | `left`), 12 px / 64 px offsets, height capped at 40 % of the display,
  never past the screen centre or into the lower 40 % (minimap / bottom HUD), moves clear of the HUD panel.
- Paging (`board/paging.js`): 3 or 4 cards per page, `1/3` label, auto-advance (`board.autoAdvanceSec`, 0 = off; paused while hovered), jump to the page of a
  new / changed / blocked / emergency card and back after 4 s, Cmd/Ctrl+Shift+PageUp/PageDown (only registered while there is more than one page), click/scroll in the window.
  Finished cards age out (`board.ageOutSec`). No operations: a tiny `작전 0` tab (or hidden with `board.hideWhenEmpty`).
- Click-through (`setIgnoreMouseEvents(true, {forward: true})`) except while the cursor is over the board; never focused (`showInactive`).
- Skin follows `glass.skin` (`cosmic` | `terminal`). `효과` only when the backend reported effect_observed; fake bot shows `시뮬`.
