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
| `sc2.direct.build_structure` | `build_structure` | placement/map resolver | contract tests; Live QA pending |
| `sc2.direct.train_unit` | `train_unit` | producer/type resolver | contract tests; Live QA pending |
| `sc2.direct.research_upgrade` | `research_upgrade` | upgrade/researcher resolver | contract tests; Live QA pending |
| `sc2.direct.warp_in` | `warp_in` | warp structure/type/placement resolver | contract tests; Live QA pending |
| `sc2.direct.move_group` | `move_group` | semantic squad selection | contract tests; Live QA pending |
| `sc2.direct.attack_move` | `attack_move` | semantic squad selection | contract tests; Live QA pending |
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
