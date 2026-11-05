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

## Bar

Orb · one-line transcript · input (`명령`) · `후퇴` · gear. Operation cards: name, one-word tier
(매크로/작전/마이크로/긴급), five-dot rail (들음 이해 배정 명령 효과), one-word blocked reason (`병력부족`, `자원부족`,
`충돌`, `거절` … raw backend text only as hover title). `시뮬` = executed by the fake bot (`direct_runtime`), `미확인` =
backend did not say. A stage is done only when the backend reports it; **효과 appears only for `effect_observed`**.

Gear (or `Cmd/Ctrl+,`) opens settings in the same glass, instantly; Esc / gear / click outside closes it; the bar stays
pinned open meanwhile. Tabs: 유리 레이아웃 무지개 음성 서버 초기화. Settings apply live, persist in
`userData/settings.json`, and are schema-validated (`renderer/settings.js`, range errors throw, never clamp).

## Cosmic glass

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

`window.commander`: `status runtimeStatus capabilities registry registerTargetPin events sendCommand`,
`onEvent(cb)` (`voice-state | transcript | intent | dock-state | settings | session`), `voice.{start stop state sendAudio
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

`npm test` runs the pure unit tests (validators, settings schema, dock and multi-display logic, voice state machine
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
