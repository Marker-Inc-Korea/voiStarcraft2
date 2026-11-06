# SC2 ActionResult: reading the game's rejection of an order

Handoff item 3 of `docs/sc2-api-doc-verification.md` §6: "ActionResult never consumed". Before this change the
product told the user an order was "issued" even when SC2 itself refused it (not enough minerals, supply
blocked, cannot place there, no energy, ...). Everything below is **offline-verified only**
(`tests/test_sc2_action_results.py`, fakes in `scripts/sc2_accuracy/harness.py`). No live SC2 evidence exists
for this change; see §6.

## 1. The two channels SC2 answers in

| Channel | Protocol field | python-sc2 | Carries |
|---|---|---|---|
| request response | `ResponseAction.result` (`repeated ActionResult`, one per submitted `Action`) | `Client.actions(actions, return_successes=True)` (default drops `Success`) | `Success` or an error code, no unit tag |
| observation | `ResponseObservation.action_errors` (`ActionError{unit_tag, ability_id, result}`, "equivalent of UI red text") | `bot.state.action_errors` (this frame + previous frame) | error code for one unit/ability |

`BotAI._do_actions` discards the return value of `Client.actions`, and nothing read `state.action_errors`.

## 2. Where the pipeline captures results

```
adapter (PythonSC2BotAdapter)            executor (SC2RuntimeExecutor)          session / UI
 _issue_unit_order / _bot_operation  ->  _execute_with_bot:                   ->  SC2PlanExecutionResult
   registers every UnitCommand queued        audit["game_results"][idx]            .audit["game_results"]
   by bot.do()  (GameResultTracker)          action_reports[idx]["game_result"]    .audit["action_reports"][i]["game_result"]
 _ensure_client_tap: wraps                   errors += GameRejectedOrder           .errors[exception_type=GameRejectedOrder]
   bot.client.actions once; maps result i  observe_direct_commands:               lease.evidence["game_result"]
   to the i-th combine_actions() raw         collect_game_results(frame)           lease.release_reason = "game_rejected"
 collect_game_results(frame): reads          fails the lease on rejection          event memory: status "blocked"
   bot.state.action_errors once/loop         holds order_issued completion
```

* `starcraft_commander/sc2_action_results.py` is pure (no SC2 import at module load): the vocabulary and the
  `GameResultTracker` ledger. Orders are registered at queue time with `(unit_tag, ability ids incl. the
  generic-redirect alias, game_loop)`.
* **Request channel**: the tap on `client.actions` asks python-sc2 for all results and attaches result *i* to
  the unit tags of the *i*-th raw action. A reply whose length differs from the sent actions, or a
  `ProtocolError`, records nothing (orders stay unknown).
* **Observation channel**: `collect_game_results` runs once per game loop from `observe_direct_commands`
  (the loop `on_step` already calls through `tick_direct_commands`). Errors are matched by
  `(unit_tag, ability_id)` within `response_window_loops` (112 loops, about 5 s at 22.4 loops/s).
* A rejection is never overwritten by a later `Success`; an error after a `Success` response overrides it.
* **Completion hold**: `order_issued` used to complete a lease on the very next tick. While the game has not
  answered every order, or answered in the current loop (an `action_errors` entry may arrive one loop later),
  completion is held (`DirectCommandLifecycle.hold_completion`, bounded by the response window; TTL expiry is
  never delayed). After the window the lease may complete with `game_result.accepted == null`.
* Plan-level: an order the game rejected in the request response moves that action from `applied` to
  `skipped`, adds a `GameRejectedOrder` error, and the narrator reports it as blocked/partial.

## 3. Field contract for the UI (additive)

`game_result` mapping (all keys always present when the mapping exists; absent entirely when no real game
order was queued, e.g. the deterministic journeys and dry-run fakes without `bot.actions`):

| key | type | meaning |
|---|---|---|
| `accepted` | `true` / `false` / `null` | `true` only with an explicit `Success` for **every** order; `false` if any order was rejected; `null` = no answer yet / unknown |
| `code` | int or null | `ActionResult` number of the first rejection, `1` for success |
| `name` | str or null | protocol name, e.g. `NotEnoughMinerals` |
| `category` | str or null | one of `resources, supply, energy, queue, placement, tech, target, range, busy, unit_state, cargo, ability, other, success` |
| `reason_ko` / `reason_en` | str or null | stable user text |
| `user_fixable` / `retryable` | bool or null | can the commander fix it by saying something else / will waiting help |
| `channel` | str or null | `request_response` or `observation_action_errors` |
| `orders`, `accepted_count`, `rejected_count`, `pending_count` | int | aggregation over the orders of this action/command |
| `last_resolved_frame` | int or null | game loop of the latest answer |

**Backend fields the UI must read** (first present wins):

1. `command_queue.*` / Direct lease: `lease.evidence.game_result` with `lease.release_reason == "game_rejected"`
   (`lease.state == "failed"`) -> stage **Blocked**, text `게임이 거부함: <reason_ko>`
   (`narrator.narrate_game_rejection`). This is the observation-channel path and arrives a few frames after the
   order was narrated as issued.
2. `result.audit.game_results[<action index>]` and `result.audit.action_reports[<i>].game_result` of the plan
   execution: `accepted == false` -> **Blocked** with `reason_ko`; `accepted == null` -> keep "unconfirmed"
   (the narration appends 게임이 주문을 받아들였는지는 아직 확인되지 않았습니다); `accepted == true` may only be shown
   as "game accepted", never as "effect observed".
3. Commander event memory (history / why-failed answers): an event with `status == "blocked"`,
   `narration == "게임이 거부함: ..."` and `detail.game_result`.

The existing `ui_status_contract` (`docs/intent-fidelity-audit.md` §6) is unchanged: a rejection is one more
`blocked` reason; `order_issued` still means "handed to python-sc2", and `game_result.accepted == true` is the
only proof the game accepted it.

## 4. Mapping table (every `error_pb2.ActionResult` value)

Generated rules live in `sc2_action_results.py`; the test `test_every_protocol_value_is_described_...`
enumerates the installed protocol (214 values in pys2clientprotocol 1.0.2) and fails for any unmapped value, so
a protocol upgrade cannot add silent unknowns. Unknown/future numbers map to
`게임이 주문을 거부했습니다 (코드 N)` / `the game rejected the order (code N)`, category `other`.

| Family | Values | category | user_fixable | retryable |
|---|---|---|---|---|
| success | `Success` | success | no | no |
| minerals/gas/terrazine/custom | `NotEnough{Minerals,Vespene,Terrazine,Custom}` | resources | yes | yes |
| supply | `NotEnoughFood` | supply | yes | yes |
| energy | `NotEnoughEnergy`, `NotEnoughCharges` | energy | yes | yes |
| life/shields | `NotEnough{Life,Shields}` | unit_state | no | yes |
| too much | `TooMuch*` | resources/supply/energy/unit_state | no | no |
| suppressed | `{Life,Shields,Energy}Suppressed` | unit_state/energy | no | yes |
| queue | `QueueIsFull`, `OrderQueueIsFull` (retry), `CantQueueThatOrder`, `RallyQueueIsFull`, `WaypointListFull` | queue | yes | queue-full only |
| placement | `CantBuild*`, `CantFindPlacementLocation`, `CantSeeBuildLocation`, `CantLand*`, `CantWarpInUnitsThere`, `AddOnTooFarFromBuilding`, `CantTrainTooFarFromTrainPowerSource`, `CantFindTeleportLocation` | placement | yes | only "cannot see" / "no location found" |
| tech | `TechRequirementsNotMet`, `BuildTechRequirementsNotMet`, `MustBuildRefineryFirst` | tech | yes | no |
| range | `TargetIsOutOfRange`, `TargetIsTooClose`, `TargetIsOutOfArc` (retry), `CouldntReachTarget` | range | yes | mostly |
| busy | `NotWhileOccupied`, `Cooldown`, `BuildingIsUnderConstruction`, `NoCalldownAvailable`, `AlreadySpawningLarva` | busy | yes | yes |
| target | `MustTarget*` / `CantTarget*` (all 90+ attribute pairs), `CantLockdown`, `CantMindControl`, `RequiresHealTarget`, `RequiresRepairTarget`, harvest/drop-off errors | target | yes | no |
| unit/cargo/other | `UnitCantMove`, `CouldntAllocateUnit`, `You*Unit`, cargo family, item/inventory family, `Error`, `NotSupported`, `Retry`, UI-only errors | unit_state/cargo/other/ability | varies | `Retry`, `CouldntAllocateUnit` |

Exact Korean/English strings and flags per value: `_EXPLICIT`, `_RESOURCES`, `_TARGET_NOUNS` in the module.

## 5. Offline scenarios (tests/test_sc2_action_results.py)

14 realistic scenarios, each driven through both channels: cannot afford marine, supply blocked, no energy for
cloak, not enough life for stim, depot placement invalid / not visible, Yamato out of range, need tech lab, unit
busy, queue full (production and order), invalid target (cloaked), unit cannot move, generic `Error`. Plus:
no response -> `accepted == null` and the lease neither completes nor fails; `Success` only completes after a
later observation without an error; short reply / protocol error stay unknown; unrelated unit errors do not leak.

## 6. What only a live SC2 can confirm

* Which result arrives in which channel for a given order. The protocol only says `action_errors` is the UI red
  text and `ResponseAction.result` is per submitted action; whether e.g. `NotEnoughMinerals` shows up in the
  request response, in `action_errors`, or in both is not documented offline.
* Timing between `RequestAction` and the observation carrying `action_errors` (assumed 0-1 game loops; the
  112-loop window and the one-loop completion hold are conservative guesses, not measurements).
* That the request-response list is positionally aligned with `combine_actions()` output (assumed from
  python-sc2's own request construction; the tap checks the length and drops mismatches).
* `ActionError.ability_id` generic-vs-exact ids for each ability (the adapter registers both the exact id and
  `GENERIC_REDIRECT_ABILITIES`' generic id).
* `ActionError.unit_tag` is "only populated when using the raw interface" (proto comment); it is the raw
  interface here. For multi-unit orders the game may report one error per unit or one for the group.
* `prevent_double_actions` can drop an order before sending; such orders never receive an answer and stay
  `accepted == null` (shown as unconfirmed, never as accepted).
* `bot.state.action_errors` merges this and the previous frame (python-sc2 behaviour), so the same error can be
  seen twice; matching is idempotent.
