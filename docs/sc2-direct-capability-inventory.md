# Direct SC2 MCP capability inventory

This inventory is the implementation boundary for the approved Direct route.
It is intentionally semantic: providers select a capability and validated
subjects/targets; they do not call arbitrary `python-sc2` or raw s2client
methods. The inventory must be extended when another runtime capability is
made safe and testable.

| MCP tool | SC2 action | Adapter boundary | Runtime proof |
| --- | --- | --- | --- |
| `sc2.direct.assign_workers` | `assign_workers` | worker/resource resolver | contract tests; Live QA pending |
| `sc2.direct.gather_resource` | `gather_resource` | explicit worker gather resolver | contract tests; Live QA pending |
| `sc2.direct.build_structure` | `build_structure` | placement/map resolver + pinned builder tag | adapter and MCP lease contract tests; Live QA pending |
| `sc2.direct.train_unit` | `train_unit` | producer/type resolver + pinned producer tag | adapter and MCP lease contract tests; Live QA pending |
| `sc2.direct.research_upgrade` | `research_upgrade` | upgrade/researcher resolver + pinned structure tag | adapter contract tests; Live QA pending |
| `sc2.direct.warp_in` | `warp_in` | warp structure/type/placement resolver + pinned structure tag | adapter contract tests; Live QA pending |
| `sc2.direct.move_group` | `move_group` | semantic squad selection | contract tests; Live QA pending |
| `sc2.direct.attack_move` | `attack_move` | semantic squad selection | contract tests; Live QA pending |
| `sc2.direct.smart` | `smart` | semantic squad selection and target resolver | contract tests; Live QA pending |
| `sc2.direct.patrol` | `patrol` | semantic squad selection | contract tests; Live QA pending |
| `sc2.direct.return_resource` | `return_resource` | semantic worker selection | contract tests; Live QA pending |
| `sc2.direct.repair` | `repair` | damaged-own-target resolver + SCV selection | contract tests; companion clarification display covered; answer-to-selection Live E2E pending |
| `sc2.direct.execute_ability` | `execute_ability` | ability and caster resolver | contract tests; Live QA pending |
| `sc2.direct.observe` | `observe` | structured state resolver | contract tests; adapter evidence + demo game-loop seam; Live QA pending |
| `sc2.direct.move_camera` | `move_camera` | semantic map target resolver | contract tests; Live QA pending |
| `sc2.direct.stop_group` | `stop_group` | semantic squad selection | contract tests; Live QA pending |
| `sc2.direct.hold_position` | `hold_position` | semantic squad selection | contract tests; Live QA pending |
| `sc2.direct.query_ability` | `query_ability` | read-only caster selection + `get_available_abilities`/`can_cast` | adapter contract tests; Live QA pending |
| `sc2.direct.query_placement` | `query_placement` | read-only `find_placement`/`can_place` validation around a semantic target | adapter contract tests; Live QA pending |
| `sc2.direct.query_unit` | `query_unit` | bounded read-only Unit calculations/predicates with semantic subject/target resolution | adapter contract tests; Live QA pending |
| `sc2.direct.query_game` | `query_game` | allowlisted read-only BotAI game calculations (cost, affordability, pending/progress) with semantic subject resolution | adapter/router/MCP contract tests; Live QA pending |
| `sc2.direct.query_map` | `query_map` | allowlisted read-only BotAI map predicates and next-expansion lookup with semantic target resolution | adapter/router/MCP contract tests; Live QA pending |
| `sc2.direct.build_gas` | `build_gas` | explicit semantic geyser + pinned worker `Unit.build_gas` | adapter contract tests; Live QA pending |
| `sc2.direct.distribute_workers` | `distribute_workers` | bounded resource-ratio BotAI coordination | adapter contract tests; Live QA pending |
| `sc2.direct.expand_now` | `expand_now` | validated own expansion anchor + pinned-worker `Unit.build` | adapter contract tests; Live QA pending |
| `sc2.direct.retreat` | `move_group` to `self_main` | emergency retreat lowering | contract tests; Live QA pending |

The current registry is therefore a complete catalog of this repository's
bounded semantic capabilities (the `retreat` capability lowers to `move_group`),
but not a claim that every raw `python-sc2`/s2client API has been exposed. Unsupported raw APIs remain out of the LLM surface until
they have a semantic schema, safety/ownership rules, adapter implementation,
and focused tests.

The read-only `sc2.direct.api_inventory` MCP tool reports the installed
`python-sc2` callable surface and its disposition. It is an audit surface,
not a raw invocation escape hatch: Unit order methods are mapped to semantic
actions, bounded Unit/BotAI calculations are mapped to query capabilities,
runtime callbacks/helpers are marked internal, `do` and `synchronous_do` are
explicitly rejected with safety reasons, and `alert`/`chat_send` are recorded as
unimplemented until a bounded schema exists. An unclassified public Unit or
BotAI callable makes `classification_complete` false (`unit_unclassified` or
`bot_unclassified`). This is a dependency-drift diagnostic, not a runtime
execution gate. `classification_complete` means every discovered callable
has a disposition; it does not mean every API is implemented or Live QA-proven.

The installed python-sc2 `Unit` order surface was rechecked on 2026-09-30:
`attack`, `build`, `build_gas`, `gather`, `hold_position`, `move`, `patrol`,
`repair`, `research`, `return_resource`, `smart`, `stop`, `train`, and
`warp_in` are all covered by the semantic action catalog or the bounded
`execute_ability`/query boundary. Generic BotAI `do` and `synchronous_do` are
deliberately not exposed because accepting arbitrary ability or method objects
would bypass the semantic target, ownership, lifecycle, and fail-closed gates.

## Cockpit discovery and semantic target-pin registration

The local Web GUI exposes the same bounded catalog and configured semantic
registry without granting browser clients a raw SC2 control surface:

- `GET /api/direct/capabilities` returns the catalog plus the current registry.
- `GET /api/direct/registry` returns named squads and target pins only.
- `POST /api/direct/registry/target-pin` stores a controller-selected semantic
  pin. The body is `{ "name": "...", "target": "enemy_natural", "metadata":
  {"source": "controller"} }`; aliases are canonicalised and raw coordinates,
  unit tags, and nested equivalents are rejected at the HTTP boundary.

The compact companion UI uses this POST path for controller-created pins. It
offers only the bounded semantic target vocabulary and never asks the user for
runtime coordinates or unit tags. A successful response is `201 Created` with
the canonical `target_pin`; duplicate names and unsupported targets are
reported as `400 Bad Request`.

Target-pin metadata is preserved in the lowered Direct action so a live map
resolver can validate it against the current observation. These endpoints do
not claim that a pin has resolved to live coordinates; map visibility,
obstacle/buildability, and unit-tag resolution remain runtime evidence.

## Concrete producer/build ownership evidence

The Direct MCP admission path calls the adapter's `bind_direct_action()` before
creating a lease. Builder workers and production/research/warp structures with
live positive tags are therefore recorded in `owned_unit_tags`; the adapter
dispatches the order back to those exact tags rather than falling back to
`BotAI.build()` or a reordered producer collection. A dependent `build ->
building_completed -> train` workflow rebinds its child plan on the later
observation frame and records the current producer tag in the child lease.

The contract is covered by
`RegistryConcreteOwnershipIntegrationTest` and the adapter reorder tests. It
is still fake-runtime evidence: actual SC2 observation churn, construction
completion, and producer behavior require Live QA.

## Raw API gap ledger (repository virtualenv)

The installed `python-sc2` package was inspected on 2026-09-28. Its public
`Unit` command methods include `attack`, `build`, `build_gas`, `gather`,
`hold_position`, `move`, `patrol`, `repair`, `research`, `return_resource`,
`smart`, `stop`, `train`, and `warp_in`. The semantic catalog maps the safe
subset of these methods through validated action types; explicit `train`
producer selection and the complete target/caster matrix for `research`,
`warp_in`, and `execute_ability` still require runtime proof.

The public `BotAI` control surface also includes `do`, `synchronous_do`,
`can_place`, `can_place_single`,
`find_placement`, `select_build_worker`, `distribute_workers`, `expand_now`,
`research`, `train`, `build`, `on_step`, and event callbacks. Placement
validation is exposed only through the bounded read-only
`sc2.direct.query_placement` tool. `build_gas`, `distribute_workers`, and
`expand_now` are now exposed through bounded semantic tools with explicit
target/range/ownership checks; the other methods remain runtime coordination
or observation APIs, not independently exposed LLM tools.
`expand_now` does not call python-sc2's `BotAI.expand_now` convenience method:
that method selects an arbitrary worker and discards the boolean returned by
`BotAI.build`. Direct expansion first binds a concrete worker tag, validates the
semantic expansion point, and issues `Unit.build(COMMANDCENTER, point)` through
the same ownership and placement path as `build_structure`. Missing tags,
invalid structure types, unavailable workers, or failed orders are refusals;
they are never converted to a successful no-op. `get_available_abilities` and `can_cast` are intentionally exposed only
through the bounded, read-only `sc2.direct.query_ability` capability; it never
issues an order or reserves a unit. The remaining APIs stay
**unimplemented as direct MCP capabilities** until each has a bounded schema,
ownership/parallelism policy, failure semantics, adapter path, and focused
contract test. Read-only `Unit` properties and calculations are likewise not
silently advertised as action tools; `sc2.direct.observe` remains the
structured state snapshot boundary.

This ledger is intentionally an explicit gap report, not a claim of “all SC2
API support”. The approved “all functions” requirement remains open until the
gap entries are either implemented and tested or explicitly rejected with a
user-visible reason.

`sc2.direct.query_placement` is likewise read-only and parallel-safe. It accepts
one semantic structure name (`subject`) and target alias, bounds placement
search to the optional `metadata.placement_policy.radius` (maximum 20 tiles),
and reports a selected tile or an explicit refusal. It never invokes `build`,
`build_gas`, `expand_now`, worker selection, or unit orders; missing placement
predicates are surfaced as `missing_placement_capability`.

`sc2.direct.query_unit` is also read-only and parallel-safe. It accepts the
bounded query enum (`calculate_speed`, distance/range/facing predicates,
ability/buff predicates, and damage/DPS calculations), plus query metadata for
`ability`, `buff`, finite `bonus_distance` (0..1000), and strict damage flags
`ignore_armor`/`include_overkill_damage`. Provider, compact, and MCP schemas
carry the same fields and fail closed when a required predicate argument is
missing. It resolves semantic own aliases (`self`/`own`/`friendly`) or enemy
aliases from the current observation, refuses missing or ambiguous targets, and
never issues an order or accepts raw unit tags. Query-only plans do not create
Direct ownership leases.

`sc2.direct.query_game` is the companion read-only, parallel-safe game-level
query boundary. Its finite enum covers `can_afford`, cost/value/supply,
pending counts, structure/tech progress, and `worker_en_route_to_build`.
The semantic subject resolves to a `UnitTypeId` or `UpgradeId` for
`already_pending_upgrade`; the router preserves upgrade tokens and unit-only
queries reject an explicit upgrade subject. Cost values are returned as
JSON-safe scalar objects. Unsupported methods, unresolved subjects,
exceptions, and non-finite values fail closed. It never issues an order or
creates a lease.

`sc2.direct.query_map` is the bounded map-level read-only companion. Its finite
enum covers `get_next_expansion`, terrain height/Z height, creep, map bounds,
pathing/placement grids, and visibility. `get_next_expansion` is map-global;
the other predicates resolve a semantic target through the map resolver before
calling BotAI. Point results are returned as JSON-safe `{ "x", "y" }` objects.
Unsupported queries, missing or unresolved targets, exceptions, and non-finite
results fail closed. It never issues an order or creates an ownership lease.

`sc2.direct.query_ability` likewise keeps concrete caster tags internal to the
adapter. Its response reports semantic unit types and boolean availability;
raw tags may be used for internal observation correlation but are not returned
through MCP or the companion UI.
