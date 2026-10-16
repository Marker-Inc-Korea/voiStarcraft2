# voiStarcraft2 Electron shell

This is a thin desktop control surface. Renderer code receives only the
allowlisted `window.commander` API from `preload.js`; Python Commander and SC2
lifecycles remain authoritative in the existing local HTTP service.

Start the Commander web service on `127.0.0.1:8765`, then run `npm install`
and `npm start`. Set `VOI_COMMANDER_URL` to use another local endpoint.

The shell deliberately does not expose raw Node, Python, unit tags, coordinates,
or StarCraft methods to the renderer. IPC command text is bounded and validated
before forwarding to `/api/micromachine/modulate` (the same Python routing
entrypoint as the browser companion). Backend bearer tokens, when needed, are
read from `VOI_COMMANDER_TOKEN` in the main process only.

The allowlisted event method polls `/api/history` with a monotonic `after` cursor,
so lifecycle and clarification events can be rendered without exposing a raw
HTTP client in the renderer.

Validation: `npm test` runs the IPC schema and loopback origin boundary checks.
This shell is an initial migration surface, not full browser parity: registry
editing, voice capture, lifecycle presentation, packaging and live SC2 desktop
QA remain pending. Keep the browser companion available until those pass.
