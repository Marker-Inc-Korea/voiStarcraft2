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

A bottom-anchored **trapezoid** control HUD (wide base on the screen edge, narrower far edge; slant 0-30°, default 14°, 유리 > 기울기).
`renderer/hud_shape.js` is the single pure geometry: it produces the CSS `clip-path`, the SVG outline + corner brackets and
the polygon main uses for pointer hit-testing, so the slanted corners and everything outside the trapezoid stay click-through
to the game (`main.js` `pointerOverPanel`, not the rectangle). Words only: orb · one-line transcript · `작전 N` (count of live
operations) · input (`명령`) · **`후퇴` `홀드` `멈춰`** · gear. **There are no operation cards in the HUD**: the renderer still derives
them from backend evidence (`stages.js` + `board_cards.js`: heard/understood/assigned/ordered/effect; 효과 only for
`effect_observed`, `시뮬` for the fake bot) and publishes them through `window.commander.board.publish(cards)` -> `board:publish`
to the tactical board window (`electron/board*`, separate small translucent window, upper-left).

Gear (or `Cmd/Ctrl+,`) opens settings in the same glass, instantly; Esc / gear / click outside closes it; the HUD stays
pinned open meanwhile. Tabs: 유리 레이아웃 무지개 음성 긴급 (전술판) 서버 초기화. Settings apply live, persist in
`userData/settings.json`, and are schema-validated (`renderer/settings.js`, range errors throw, never clamp).

### Emergency parity

The three words are always visible, large (min 76×50), and have global shortcuts that take the **same route**:
button or shortcut -> `window.commander.emergency(action)` -> IPC `commander:emergency` (enum `retreat|hold|stop`, validated
in `protocol.js`) -> `POST /api/emergency {action}` (deterministic, no model) -> result word on the button (`…` / `전달` /
`실패`) and one emergency card to the board. Defaults `Cmd/Ctrl+Shift+R` retreat, `+H` hold, `+X` stop (설정 > 긴급). A shortcut that
cannot be registered shows `키불가` on its button and in settings; it is never silent. Registration logic is pure and tested
(`shortcuts.js`, fake `globalShortcut`).

### Skins (유리 > 스킨)

`cosmic` (우주): the glass + starfield/nebula described below, adapted to the trapezoid. `terminal` (터미널): StarCraft
in-game computer console: near-black translucent panel, phosphor green (`#2cff7a`) monospace (`SF Mono`/Menlo/IBM Plex Mono),
scanlines, faint CRT glow/vignette/flicker, blinking block cursor (`> 명령_`), chamfered Terran key bevels and corner brackets,
green status LEDs. Both skins share opacity/blur/hue settings, reduced-motion support and the rainbow edge overlay. The
terminal text scrim/glow are computed from the opacity (`renderer/skins.js`) so it stays legible at 8 % opacity. The switch is
live (no reload) and persisted (`glass.skin`).

## Cosmic glass (skin 우주)

Inside the panel, on one transparent canvas drawn additively (`lighter` + `mix-blend-mode: plus-lighter`, never opaque):
3-depth parallax starfield (twinkle, 4-point flares, a shooting star every ~8-14 s), faint constellation lines, drifting
nebula clouds (75 s loop, recoloured by 색조) and an aurora shimmer on the top edge. Stars/nebula use their own sliders
(별 / 별 속도 / 성운 / 반짝임 / 유성), so they stay visible at low glass opacity. Finish: 1px white→violet→cyan gradient rim,
inner highlight, starlight glow, hover specular sweep; the mic orb is a small planet with an orbiting ring and moon; rail
dots are tiny stars (done = bright halo, active = pulsing flare, blocked = red dwarf, sim = hollow dashed). One rAF loop
capped at ~30 fps, paused while the bar is collapsed, a single still frame under reduced motion. Pure model: `renderer/sky.js`.

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

`npm test` runs the pure unit tests (validators, HUD trapezoid geometry + polygon hit-test, skins, emergency shortcut registry, board card view-model, settings schema, dock and multi-display logic, voice state machine
and client on a fake `ws`, stage derivation, window flags). They never launch Electron or touch the pointer.

The real-Electron smoke runs take over the mouse pointer and capture the screen, so run them only when the Mac is
otherwise idle. Screenshots go to a temp dir unless `SMOKE_SAVE_SHOTS=1`; saved captures show your desktop and are
git-ignored (`docs/screens/`, `docs/e2e/*.png`).

```
swiftc -O smoke/warp_cursor.swift -o /tmp/warp_cursor
SMOKE_DISPLAY=primary npx electron smoke/driver.js      # also: SMOKE_DISPLAY=1, SMOKE_DISPLAY=2
npx electron smoke/overlay_shots.js

# real end-to-end: real web_gui --dry-run + real Realtime proxy + TTS wav as the Chromium mic (docs/e2e/RESULTS.md)
E2E_NAME=u1_scout_attack VOI_COMMANDER_URL=http://127.0.0.1:18765 npx electron smoke/e2e_real.js
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
