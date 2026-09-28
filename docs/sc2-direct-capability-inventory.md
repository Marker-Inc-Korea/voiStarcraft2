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
| `sc2.direct.repair` | `repair` | damaged-own-target resolver + SCV selection | contract tests; ambiguity UX pending |
| `sc2.direct.execute_ability` | `execute_ability` | ability and caster resolver | contract tests; Live QA pending |
| `sc2.direct.observe` | `observe` | structured state resolver | contract tests; per-frame watcher pending |
| `sc2.direct.move_camera` | `move_camera` | semantic map target resolver | contract tests; Live QA pending |
| `sc2.direct.stop_group` | `stop_group` | semantic squad selection | contract tests; Live QA pending |
| `sc2.direct.hold_position` | `hold_position` | semantic squad selection | contract tests; Live QA pending |
| `sc2.direct.retreat` | `move_group` to `self_main` | emergency retreat lowering | contract tests; Live QA pending |

The current registry is therefore a complete catalog of this repository's
semantic adapter methods, but not a claim that every raw `python-sc2`/s2client
API has been exposed. Unsupported raw APIs remain out of the LLM surface until
they have a semantic schema, safety/ownership rules, adapter implementation,
and focused tests.

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
subset of these methods through validated action types; `build_gas`, explicit
`train` producer selection, and the complete target/caster matrix for
`research`, `warp_in`, and `execute_ability` still require runtime proof.

The public `BotAI` control surface also includes `do`, `synchronous_do`,
`get_available_abilities`, `can_cast`, `can_place`, `can_place_single`,
`find_placement`, `select_build_worker`, `distribute_workers`, `expand_now`,
`research`, `train`, `build`, `on_step`, and event callbacks. These are runtime
coordination or observation APIs, not independently exposed LLM tools. They
remain **unimplemented as direct MCP capabilities** until each has a bounded
schema, ownership/parallelism policy, failure semantics, adapter path, and
focused contract test. Read-only `Unit` properties and calculations are
likewise not silently advertised as action tools; `sc2.direct.observe` is the
current structured observation boundary.

This ledger is intentionally an explicit gap report, not a claim of “all SC2
API support”. The approved “all functions” requirement remains open until the
gap entries are either implemented and tested or explicitly rejected with a
user-visible reason.
