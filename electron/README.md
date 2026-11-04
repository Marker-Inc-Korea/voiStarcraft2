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

The allowlisted event method polls `/api/history` with a monotonic `after` cursor
and adopts the server's `latest` watermark, so lifecycle and clarification
events can be rendered without exposing a raw HTTP client in the renderer. The
shell also exposes the read-only MicroMachine and runtime status endpoints,
semantic registry data, and a target selector limited to known semantic pins;
coordinates, unit tags, and other raw runtime identities are rejected in
preload/Main before they reach Python.

Validation: `npm test` runs the IPC schema and loopback origin boundary checks.
This shell is an initial migration surface: voice capture, packaging/signing,
and live SC2 desktop QA remain pending. Keep the browser companion available
until those pass. Contract tests cover the typed IPC and semantic target-pin
boundary; they are not real-game evidence.
