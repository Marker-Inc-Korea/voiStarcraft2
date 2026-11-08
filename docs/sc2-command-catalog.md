# SC2 command catalog (Terran, 103 commands)

Offline evidence only (s2client-api header extract + burnysc2 tables + fake bots). Nothing in this document proves a
live StarCraft II accepted an order; see "What only a live SC2 can confirm".

## Public API

Module `starcraft_commander/sc2_catalog.py` (pure; never talks to the game). Facts are generated into
`starcraft_commander/sc2_catalog_data.py` by `python -m scripts.sc2_accuracy.gen_catalog_facts` (`--check` fails when stale).

| Function | Purpose |
|---|---|
| `all_commands() -> tuple[CommandEntry, ...]` | all 103 entries |
| `get_command(id) -> CommandEntry` | by stable id, e.g. `research_terraninfantryweapons` (`KeyError` if unknown) |
| `commands_by_category(cat)` | `train build research morph effect behavior other` |
| `commands_for_issuer("TERRAN_SIEGETANK")` | everything a unit/structure type can issue |
| `find_by_alias(text, category=None) -> tuple[CommandEntry,...]` | exact (normalised) Korean/English alias, id, header name or token; several results = ambiguous (ask once or pick by state) |
| `search(text, limit=5)` | ranked free-text match (exact alias first, then alias contained in the phrase) |
| `CatalogState(owned=(Owned(...),), upgrades_done, upgrades_pending, enemies_visible, free_geysers, mineral_patches, can_afford)` | state snapshot; `PythonSC2BotAdapter.catalog_state()` builds it from the live observation |
| `evaluate(id, state, issuer=None, upgrade=None) -> CommandEvaluation` | `ok`, issuer tags/type, chosen upgrade level (research), exact ability; or `reason` (round-4 refusal code, e.g. `missing_prerequisite: ARMORY`) + `reason_ko` |
| `issuable_now(state, categories=None)` | every command accepted by the snapshot |
| `direct_task_payload(id, issuer=None, location="", level=None)` | provider-output payload that lowers the command through the REAL compile -> route -> executor chain |
| `catalog_tool_enums()` | catalog-derived enums for the realtime tool schema / prompt |
| `refusal_korean(reason)` | Korean sentence for a refusal code |

`CommandEntry` fields: `id, category, header_name, header_id, target_kind (none|point|unit|point_or_unit), target_noun,
target_mode, issuers, abilities{issuer: AbilityId name}, tokens{issuer: Direct token}, aliases_ko, aliases_en,
description_ko, effect, energy_cost, produces, upgrades (levels), upgrade_requirements, unconfirmed_issuers`.
