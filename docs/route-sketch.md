# Minimap route sketch (round 6)

While the user holds Control+Option the overlay records strokes drawn with the user's own mouse over the SC2 minimap.
On release the strokes (+ what the user said) go to `POST /api/route/sketch`; a validated route is executed through the
SC2 API only. The real mouse cursor is never moved or captured by the agent; the overlay shows a separate *agent cursor*
(`agent_cursor` events) where the agent just issued an order.

## HTTP contract

`POST /api/route/sketch` (same token header / loopback rules as the other POST routes; body <= 2.4 MB):

```json
{"update_id": "u1",
 "strokes": [[[nx, ny, t_ms], ...], ...],      // minimap-FRAME normalised 0..1, origin top-left, drawing order
 "image_png_b64": "..." | null,               // crop of the minimap frame incl. the strokes (<= 1.5 MB decoded)
 "transcript": "마린 4기",                    // optional, <= 2000 chars
 "minimap_rect": {"x":0,"y":0,"w":0,"h":0}}   // screen px, diagnostic only
```

Limits (400 otherwise): <= 20 strokes, <= 2000 points total, points inside 0..1, valid base64.

`202 {"kind": "execute"|"ask"|"refuse"|"none", "plan_id", "narration_ko", "question_ko", "route", "squad"}`;
`route = {"waypoints": [{nx,ny,x,y,label_ko}], "final": {action: "move"|"attack", nx,ny,x,y,label_ko}}`,
`squad = {label_ko, unit_count}`; `route`/`squad` are `null` unless `kind == "execute"`.

| kind | when |
|---|---|
| execute | route validated; squad leased; orders queued |
| ask | ambiguous (several unlabelled strokes, no/unsatisfiable squad, avoid mark crossed, VLM low confidence/failed/garbled, VLM asked) - ask once, Korean |
| refuse | raw coordinates / prompt-injection in the transcript (before any VLM call); VLM point not on a stroke (`vlm_invented_point`); no pathable point within 8 world units of a waypoint; squad could not be leased |
| none | nothing drawn / only taps; no game attached; no map info yet |

### Events

Returned by `GET /api/history?after=N&route_after=M` as additive `route_events` + `route_latest` (the legacy
`events`/`latest` are untouched and the keys are absent without `route_after`), by `GET /api/route/events?after=M`, and
published on the SSE journal `/api/events` with `event_type` = kind. Every event carries `route_seq`, `kind`, `plan_id`:

* `route_plan` `{route, squad, narration_ko}`
* `route_progress` `{reached_index (0-based last reached waypoint, -1 none), done, released, reason?}`; the last one has
  `released: true` and `done: true` (final reached, reason `route_done`) or `done: false` with reason
  `emergency_preempted:*`, `ttl_expired`, `cancelled_by_user`, `squad_lost`.
* `agent_cursor` `{nx, ny, action: "select"|"move"|"attack", label_ko}` - first `select` at the squad centroid, then one
  per waypoint and the final point, all minimap-frame-normalised.

## Pipeline (`route_sketch.py`, pure)

1. Reject raw-control/injection transcripts (`realtime_intent.transcript_is_raw_control_or_injection`).
2. Drop noise strokes (< 0.03 frame units long).
3. Semantics: injected `VlmReader` (default `route_vlm.ProxyVlmReader`, proxy OpenAI-compatible chat + image part,
   `VOI_ROUTE_VLM_MODEL`, default `gemini-3-flash`, 20 s timeout) or the deterministic fallback when no key is set
   (one open stroke = move route; open stroke ending in a closed stroke around visible enemies = attack; anything else
   asks). The VLM decides roles (path/target/avoid/squad_select/noise), path order/direction, move vs attack and the
   squad source - never coordinates; any `nx/ny/x/y` it emits must lie within 0.02 of a stroke or the decision is `refuse`.
4. Geometry (deterministic): frame -> world (below), Ramer-Douglas-Peucker to <= 8 points per path stroke
   (total <= 8), snap to the nearest pathable point (ring search, radius 8), Korean labels (nearest semantic place within
   12 % of the map -> "<place> 근처", centre -> "맵 중앙", else "맵 중앙 기준 N시 방향").
5. Validator: target inside/at the drawn target circle, squad resolves to existing own units (named type exists, count
   satisfiable, not leased elsewhere; or the units inside the squad_select circle), avoid marks not crossed by
   squad->waypoints leg or the path, every waypoint traces to a stroke point.

## Execution (`route_execution.py`)

`DirectCommandLifecycle.pending(owned_unit_tags=squad)` -> real executor/adapter: `MOVE_GROUP(w1)`, `MOVE_GROUP(wk, queue)`,
final `MOVE_GROUP|ATTACK_MOVE(queue)` (raw: `MOVE`, `MOVE queued`..., `MOVE|ATTACK queued`; python-sc2 emits one raw command per
unit when abilities cannot be combined). Targets are registered server-side as `route:<plan_id>:<i>` in
`adapter.route_target_points`; a plan arriving over MCP cannot invent one. Progress = squad centroid within 4 world units of the next
point, evaluated on each `tick_direct_commands`. The lease is released on final reached, TTL (180 s), cancel,
emergency preemption (the existing `_direct_preflight` cancels combat-group leases, and the route reports
`emergency_preempted`) or when the whole squad is gone. Releasing does not clear SC2's order queue.

Hooks outside new modules: `python_sc2_adapter` (route target lookup, route evidence provider), `contracts._with_plan_defaults`
(kept `queue`, previously dropped for plan-level defaults), `web_gui` (endpoint, history/route events, `SessionLoopBridge.route_service`).

## Assumptions only a live game can confirm

* That SC2 draws the playable area aspect-preserved and centred in the square minimap frame (one frame unit =
  `max(width, height)` world units) - `route_sketch.frame_to_world`, with `SketchWorld.calibration` as the offset to fix.
* That the overlay's minimap rect is exactly SC2's minimap (SketchUI's calibration).
* That SC2 accepts the queued MOVE/ATTACK orders for those units in a real game state and that units actually arrive
  (offline evidence is request shape only - `scripts/sc2_accuracy/harness.py`).
* That `in_pathing_grid` snapping matches what a human means by a point on a ramp/cliff edge.
* Whether the VLM reads real minimap crops (only a synthetic PNG was sent; see RouteBench for the benchmark).
