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

`npm test` (83 tests: validators, settings schema, dock logic, voice state machine + client on a fake `ws`, stage
derivation, window flags). Real-Electron smoke against a fake backend (not SC2 evidence):

```
swiftc -O smoke/warp_cursor.swift -o /tmp/warp_cursor   # real cursor moves via CoreGraphics
npx electron smoke/driver.js          # 57 checks + docs/screens/*.png
npx electron smoke/overlay_shots.js   # edge glow over bright/dark backdrops
```

The microphone in smoke is Chromium's fake device; real speech, real SC2 and macOS fullscreen-app overlay are unverified.
