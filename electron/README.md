# voiStarcraft2 Electron shell

This is a thin desktop control surface. Renderer code receives only the
allowlisted `window.commander` API from `preload.js`; Python Commander and SC2
lifecycles remain authoritative in the existing local HTTP service.

Start the Commander web service on `127.0.0.1:8765`, then run `npm install`
and `npm start`. Set `VOI_COMMANDER_URL` to use another local endpoint.

The shell deliberately does not expose raw Node, Python, unit tags, coordinates,
or StarCraft methods to the renderer. IPC command text is bounded and validated
before forwarding to `/api/command`.
