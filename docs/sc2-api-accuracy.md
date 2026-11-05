# SC2 API accuracy audit (offline)

**Scope and honesty.** StarCraft II is not installed on the machine that produced this audit.
Everything here is **offline evidence**: ids, names, target kinds and the shape of the raw
protobuf requests the product would send. **Nothing below proves that an ability fired, that SC2
accepted an order in a given game state, or that any live behaviour is correct.** Live fidelity is
still unproven; section 6 is the checklist for proving it.

Reproduce everything:

```bash
PYTHONPATH=. .venv/bin/python -m scripts.sc2_accuracy.audit --json /tmp/sc2-audit.json --markdown /tmp/sc2-audit.md
PYTHONPATH=. .venv/bin/python -m pytest tests/test_sc2_api_accuracy.py -q
```

The audit and tests skip (not fail) when `stableid.json` or burnysc2 is absent; set
`VOI_SC2_STABLEID=/path/to/stableid.json` to point at the file.

## 1. Data sources and versions

| Source | What it gives | Version / provenance |
|---|---|---|
| `~/Library/Application Support/Blizzard/StarCraft II/stableid.json` | Units 2010, Abilities 4137, Upgrades 306, Buffs 307, Effects 13 (ids + names + remap ids) | file mtime 2026-09-25 01:06 local (written 4 min after `ExecuteInfo.txt`, i.e. by the Battle.net client install/update on that date) |
| burnysc2 `sc2.ids`, `sc2.dicts` | `UnitTypeId`/`AbilityId`/`UpgradeId` enums the Direct adapter really calls, unit→ability lists, train/research tables | burnysc2 **7.3.0**; `sc2.ids.id_version.ID_VERSION_STRING = 4.11.4.78285` (ids generated from game data **4.11.4**); `sc2/versions.py` lists base builds up to **94137** |
| `s2clientprotocol` | raw `RequestAction` / `ActionRawUnitCommand` protobufs | dist `pys2clientprotocol` **1.0.2**, protobuf **7.35.1** |
| `scripts/sc2_accuracy/s2client_api_enums.json` | ids + ability **target kinds** + per-unit ability lists from Blizzard's `sc2_typeenums.h` (the C++ enum MicroMachine compiles against) | Blizzard/s2client-api commit `614acc00` (**2019-08-22**), MIT; identifier→number facts only. It is OLD: ids added later (3747, 3753) are absent and reported as header-unverified |

Cross-source consistency measured here: every `AbilityId` and `UpgradeId` id in burnysc2 exists in
`stableid.json` with the same number (0 missing); every `UnitTypeId` exists in stableid (5 stableid
units, 2005–2009, are newer than the burnysc2 enum). Six `UnitTypeId`/eight `BuffId` *names* differ
from stableid at high ids (enum renames in the generator); no product token touches them.

**Does `stableid.json` match the game build the integration targets?** The MicroMachine
integration pins **Base97364** (`starcraft_commander/sc2_launch_contract.py:7`
`REQUIRED_SC2_BASE = 97364`; README "pinned `Base97364`"). `stableid.json` carries **no build
number**, so the match cannot be proven from the file. Circumstantial: this machine's
`ExecuteInfo.txt` and `GameLogs/…SystemInfo.txt` point at `Versions/Base97364/`, and the file was
written by the same client that installed that base. burnysc2 7.3.0 knows base builds only up to
94137 (so it has no `97364` entry in `sc2/versions.py`), and its enums were generated from 4.11.4.
Treat id agreement across all three sources for the ~35 abilities the product uses as strong
evidence the ids are stable across those versions, **not** as proof for Base97364 in play.
Section 6 step 2 verifies this live.

## 2. Findings (all fixed unless listed in section 5)

The audit enumerated 45 unit/structure/pseudo tokens, 33 semantic ability tokens, 6 research pseudo-tokens and
36 CPP ability-marker strings.

| # | Defect (proven by a failing test first) | Evidence | Fix |
|---|---|---|---|
| D1 | **31 of 33 semantic abilities never resolved** (`unresolvable_ability`): `siege_mode`, `yamato`, `emp`, `snipe`, `tactical_nuke`, `marine_stimpack`, `banshee_cloak`, `ghost_cloak`, `medivac_load`, … only `stimpack` and `unsiege` worked. A voice "탱크 시즈모드" issued **nothing**. | audit before: `{UNRESOLVED: 31, ok: 1}`; `test_ability_ids_are_the_exact_ids_the_game_documents` | `SC2_SEMANTIC_ABILITIES` table in `python_sc2_adapter.py` (token → exact `AbilityId`, target mode, caster types) |
| D2 | Wrong ids in the old alias table: `YAMATO_YAMATO` and `TACNUKE_CALLDOWN` do not exist (`YAMATO_YAMATOGUN` = 401, `TACNUKESTRIKE_NUKECALLDOWN` = 1622). | `getattr(AbilityId, …) is None` | same table |
| D3 | **Count defaulted to 1**: "탱크 시즈모드" sieged one tank. | `test_router_defaults_ability_count_to_all…` | router emits `count=0` (whole group) unless a number was spoken; adapter treats 0 as every eligible caster; explicit 2 still means exactly 2 |
| D4 | **Only `unit_classes[0]` was lowered**: "스팀팩" (marine+marauder) stimmed marauders only. | `test_router_emits_one_ability_action_per_named_unit_class` | one Direct action per named unit class (`unified_command_router.py` `_append_task_actions`) |
| D5 | Target-requiring abilities were sent target-less (Yamato, Snipe, EMP, KD8, Lock-On, Nuke, Load, Heal, Auto-Turret…) — SC2 rejects these. | python-sc2 `Unit.__call__` target-kind warnings; `target_shape_ok` vs s2client-api header | per-ability target mode: enemy Unit / enemy point / own cargo / damaged bio / caster point; **refuses** (`target_required: …`) instead of sending a wrong shape |
| D6 | Caster form not enforced: "unsiege" ran on unsieged tanks, `hellion_mode` etc. | `test_siege_unsiege_only_address_the_right_form` | caster list per ability (SIEGETANKSIEGED, HELLIONTANK, WIDOWMINEBURROWED, THORAP, VIKINGASSAULT, LIBERATORAG) |
| D7 | `COMBATSHIELD` resolved to `UpgradeId.COMBATSHIELD`(234) which has **no research ability**; the game researches `SHIELDWALL`(16) (`RESEARCH_COMBATSHIELD`=731). `GHOST_CLOAK` did not resolve (real: `PERSONALCLOAKING`, Ghost Academy). | `test_research_tokens_resolve…` | `_SEMANTIC_UPGRADE_ALIASES` |
| D8 | Research addressed to the wrong building: planner says `TERRAN_BARRACKS`, the order must go to the **Barracks Tech Lab**. | `test_research_is_a_no_target_command_on_the_techlab…` | researcher derived from python-sc2 `UPGRADE_RESEARCHED_FROM` |
| D9 | SCV training refused when the base is an **Orbital Command / Planetary Fortress** (only `COMMANDCENTER` matched). | `test_train_unit_uses_the_creation_ability…[SCV-…]` | `_PRODUCER_EQUIVALENTS` |
| D10 | `WIDOWMINE` had no producer in the router (`producer: ""`) so training it always refused. | `test_every_trainable_dsl_unit_gets_the_producer…[WIDOWMINE]` | router table |
| D11 | Add-ons and Orbital/Planetary upgrades were lowered as SCV `build` with a point → `Unknown python-sc2 UnitTypeId name: 'BARRACKS_TECHLAB'` / an SCV sent `UPGRADETOORBITAL`. SC2 needs a no-target order on the idle **parent structure** (`BUILD_TECHLAB_BARRACKS`=421 …, `MORPH_ORBITALCOMMAND`=1516 = stableid `UPGRADETOORBITAL_ORBITALCOMMAND`). | `test_addons_and_townhall_morphs_are_no_target_orders…` | `_STRUCTURE_MORPHS` + `_morph_structure` |

Validated correct (no change): SiegeMode/Unsiege, EffectStim (generic 3675, remapped from 380/253),
Cloak on/off for Ghost (382/383) vs Banshee (392/393), Medivac load/unload-all/heal/afterburners,
bunker `LOAD_BUNKER`(407), `TERRANBUILD_*` build abilities on SCV (point target, refinery = geyser
unit), `COMMANDCENTERTRAIN_SCV`, `BARRACKS/FACTORY/STARPORTTRAIN_*`, Engineering Bay upgrades,
repair = unit target, stop/hold = no target, move/attack/smart/patrol = point target.

### Request-shape coverage (`tests/test_sc2_api_accuracy.py`, 100 tests)

`scripts/sc2_accuracy/harness.py` builds a **real** `sc2.game_data.GameData` from `stableid.json`
(all 2010 units / 4137 abilities / 306 upgrades, creation + research abilities from python-sc2's
own tables, add-on creation abilities added from the s2client-api header) and drives the **product's
real** `SC2RuntimeExecutor` → `PythonSC2BotAdapter` against real `sc2.unit.Unit` objects. Orders go
through the real `BotAI.do`; python-sc2's own `combine_actions` lowers them to
`ActionRawUnitCommand`, wrapped in a `RequestAction`, serialised and re-parsed. Each case asserts
`ability_id`, `unit_tags`, target kind (none / `target_world_space_pos` / `target_unit_tag`),
`queue_command=False`, no python-sc2 target-kind warning, and agreement with the s2client-api
declared target kind. Cases: 32 voice abilities (all 4 tiers' micro), 16 group orders
(move/attack/smart/patrol × 4 targets), stop/hold/retreat, 6 structure builds + refinery
(geyser unit), 8 add-on/morph orders, 7 train paths + 16 producer-equivalence checks, research
(Stimpack, Combat Shield × 3 researcher spellings), repair, load/heal/unload, tactical jump
(explicit destination required), enemy-less Yamato refused.

Not covered by request shapes: Land/Lift, ScannerSweep/MULE/Supply Drop, bunker load/unload and
Salvage, Raise/Lower depot — **the DSL cannot express them** (section 4), so there is no product
path to test.

## 3. Audit table (after fixes)

| ability token | resolves to (id) | status |
|---|---|---|
| stimpack | `EFFECT_STIM` 3675 (generic, remaps 380/253) | ok; casters Marine+Marauder |
| marine_stimpack / marauder_stimpack | `EFFECT_STIM_MARINE` 380 / `EFFECT_STIM_MARAUDER` 253 | ok |
| siege_mode / unsiege | `SIEGEMODE_SIEGEMODE` 388 / `UNSIEGE_UNSIEGE` 390 | ok |
| ghost_cloak / ghost_decloak | `BEHAVIOR_CLOAKON_GHOST` 382 / `…OFF_GHOST` 383 | ok (decloak via link-name sibling) |
| banshee_cloak / banshee_decloak | `BEHAVIOR_CLOAKON_BANSHEE` 392 / `…OFF_BANSHEE` 393 | ok |
| yamato | `YAMATO_YAMATOGUN` 401, Unit target | ok |
| emp / snipe | `EMP_EMP` 1628 (Point) / `EFFECT_GHOSTSNIPE` 2714 (Unit) | ok |
| tactical_nuke | `TACNUKESTRIKE_NUKECALLDOWN` 1622 (Point) | ok — python-sc2 `UNIT_ABILITIES` omits it for Ghost; s2client-api lists it |
| kd8_charge / lock_on | `KD8CHARGE_KD8CHARGE` 2588 / `LOCKON_LOCKON` 2350 | ok |
| widow_mine_burrow / unburrow | 2095 / 2097 | ok |
| hellbat_mode / hellion_mode | `MORPH_HELLBAT` 1998 / `MORPH_HELLION` 1978 | ok |
| thor_high_impact / explosive | 2362 / 2364 | ok |
| viking_assault / fighter | 403 / 405 | ok |
| liberator_defender / fighter | `MORPH_LIBERATORAGMODE` 2558 (**Point**) / `…AAMODE` 2560 | ok |
| medivac_load / unload_all / heal / afterburners | 394 (Unit) / 396 (Point) / 386 (Unit) / 2116 | ok |
| auto_turret | `BUILDAUTOTURRET_AUTOTURRET` 1764 (Point) | ok |
| interference_matrix / anti_armor_missile | `EFFECT_…` 3747 / 3753 | ok in stableid+burnysc2; **target kind inferred** (post-2019, not in the header) |
| tactical_jump | `EFFECT_TACTICALJUMP` 2358 (Point) | ok; refuses without a destination |

Research tokens: `STIMPACK`→`UpgradeId.STIMPACK` 15 @ Barracks Tech Lab (`BARRACKSTECHLABRESEARCH_STIMPACK` 730);
`COMBATSHIELD`→`SHIELDWALL` 16 @ Barracks Tech Lab (`RESEARCH_COMBATSHIELD` 731);
`GHOST_CLOAK`→`PERSONALCLOAKING` 25 @ Ghost Academy (820); `BANSHEE_CLOAK`→`BANSHEECLOAK` 20 @ Starport Tech Lab (790);
`YAMATO_CANNON`→`BATTLECRUISERENABLESPECIALIZATIONS` 76 @ Fusion Core (`RESEARCH_BATTLECRUISERWEAPONREFIT`). **Caveat:** stableid also has
an upgrade literally named `YamatoCannon` (258) with no research building; the product has not been
shown to need that id and the mapping to 76 is by python-sc2's research table — verify live (6.2).

Unit tokens: 28 ok. Expected non-unit pseudo-tokens (`STIMPACK`, `COMBATSHIELD`, `GHOST_CLOAK`,
`BANSHEE_CLOAK`, `YAMATO_CANNON`) have no UnitTypeId by design. `NOT_TERRAN` rows are transient
forms/add-ons the Terran *production* closure doesn't list (`SIEGETANKSIEGED`, `WIDOWMINEBURROWED`,
`THORAP`, `VIKINGASSAULT`, `LIBERATORAG`, `NUKE`, 6 add-ons); ids are identical in all three sources.

Alias collisions: **0** same-kind collisions (no alias maps to two different units/abilities);
the cross-table "dual use" rows are the same entity named in two tables, not conflicts. One semantic
overlap is documented in section 4 (hellbat→hellion).

## 4. DSL gaps (cannot be expressed; not fixed here)

* Hellbat as a *build target*: alias `hellbat` → `TERRAN_HELLION`; producing a Hellbat needs
  `TRAIN_HELLBAT`(596, Armory required) which the DSL has no token for. `hellbat_mode` (morph) works.
* Terran units in python-sc2's closure with no DSL token: Auto-Turret, Missile Turret, Sensor Tower,
  Planetary Fortress, Orbital Command (the last two *are* reachable only as `build_structure`
  subjects, which D11 now lowers correctly).
* Abilities with no semantic token: Lift/Land, ScannerSweep, Calldown MULE, Supply Drop, bunker
  load/unload-all, Salvage, supply depot raise/lower, KD8 to point, Medivac unload to point,
  Reaper grenade targets, upgrades beyond Stimpack/Combat Shield/Ghost & Banshee cloak/Yamato
  (infantry/vehicle/ship weapons+armor are reachable only as raw upgrade names through
  `research_upgrade`, e.g. `TERRANINFANTRYWEAPONSLEVEL1`, which the adapter lowers correctly on an
  Engineering Bay — tested ad hoc, not a first-class DSL token).
* Medivac unload-all and Auto-Turret use the caster position as the point unless an explicit
  target is spoken.

## 5. Handoff items (outside this branch's files)

1. `starcraft_commander/micromachine_command_execution.py:173-174` — Banshee cloak markers
   `("BEHAVIOR_CLOAKON",)`/`("BEHAVIOR_CLOAKOFF",)` are the **generic** names (3676/3677). The
   Python adapter now orders `BEHAVIOR_CLOAKON_BANSHEE` (392). The native binary contains both
   strings (`strings` on the CI binary shows `BEHAVIOR_CLOAKON_BANSHEE`), and which spelling the C++
   `AbilityTypeToName(action.abilityID)` emits for an observed order is **unverified offline**.
   `patches/0068…` tests assert `ability:CLOAKON`. Left untouched because this file feeds
   pre-live journey evidence. Verify live (6.3).
2. `…micromachine_command_execution.py:161` — `hellbat_mode` marker `MORPH_HELLIONTANK` is not an
   ability name in the 2019 header or stableid; `MORPH_HELLBAT` (first marker) is correct.
3. `…micromachine_command_execution.py:176-177` — interference matrix / anti-armor missile markers
   are ids newer than the s2client-api header (ids 3747/3753 are valid in stableid + burnysc2).
4. `starcraft_commander/policy_modulation_provider.py` / `micromachine_terran_capabilities.py` —
   `hellbat`/`hellbats` alias to `TERRAN_HELLION` (section 4).
5. `tests/test_direct_command_architecture.py::test_call_many_async_runs_independent_tools_in_parallel`
   asserts a `< 0.035 s` wall-clock bound and fails (0.050 s) when run alongside other work on this
   Mac; unrelated to this change, flaky timing assertion.

## 6. Readiness runbook — first live session (machine WITH StarCraft II)

### 6.1 Install and environment

1. Install SC2 with the Battle.net client. The integration requires the direct binary
   `…/StarCraft II/Versions/Base97364/SC2.app/Contents/MacOS/SC2` (`REQUIRED_SC2_BASE`).
   Production and soak qualification **reject Battle.net wrappers** and a different base.
2. `export SC2_ROOT="$HOME/Desktop/StarCraft2/StarCraft II"` (path may contain a space; the
   scripts alias it to `/private/tmp/voi-sc2-root`). Legacy fallback: `SC2PATH` (burnysc2).
   `SC2_EXECUTABLE` overrides the binary; `SC2_LAUNCH_MODE=direct`.
3. Maps live under `$SC2_ROOT/Maps/`. Map pool (`integrations/micromachine/MICROMACHINE_MAP_POOL.json`):
   **required** `AcropolisLE.SC2Map` (qualified_baseline); **diagnostic**
   `Ladder2019Season3/ThunderbirdLE.SC2Map` (blocked_no_production_deadlock, never counts for
   signoff); **excluded** `Custom/UnknownOrUnvetted.SC2Map`. `VOI_SC2_CREATEGAME_MAP_DATA=1` is the default.
4. `python -m pip install 'voiStarcraft2[sc2]'` (burnysc2 ≥ 6.5; this audit used 7.3.0).
5. Build the native bot (once per commit):
   `integrations/micromachine/scripts/build_macos_local.sh`.

### 6.2 Commands (from `docs/micromachine-final-live-qa.md` and `integrations/micromachine/README.md`)

```bash
integrations/micromachine/scripts/probe_macos_local.sh      # CreateGame/JoinGame yields own start units
integrations/micromachine/scripts/smoke_macos_local.sh      # MAP_FILE=AcropolisLE.SC2Map by default
QA30_MANIFEST=.local/qa-scenario-manifest-30.json \
  integrations/micromachine/scripts/live_qa_30_macos_local.sh   # 30 scenarios, 900 s each, 2 attempts
python3 -m starcraft_commander.live_qa_audit --run-root <RUN_ROOT> --manifest .local/qa-scenario-manifest-30.json
python3 -m starcraft_commander.micromachine_final_release check-status
```

QA30 scenario ids (from `scripts/fidelity/fixtures/qa30/`): 01-scv-production, 02-mineral-saturation,
03-refinery-gas, 04-supply-recovery, 05-barracks, 06-marine-wave, 07-factory, 08-factory-techlab,
09-siege-tank, 10-tank-ramp-defense, 11-tank-siege-hold, 12-marine-natural-pressure,
13-marine-main-attack, 14-marine-scout, 15-parallel-attack-defense, 16-defense-reinforcement,
17-natural-expansion, 18-expansion-workers, 19-expand-production-resume, 20-retreat, 21-cancel-attack,
22-reassign-marines, 23-reassign-tanks, 24-retarget, 25-long-defense, 26-long-production,
27-prerequisite-wait, 28-supply-block-recovery, 29-heal-regroup, 30-game-end. The manifest file
`.local/qa-scenario-manifest-30.json` is **not tracked in the repo**; regenerate/obtain it before running.
Note (`docs/intent-fidelity-audit.md`): the recorded QA30 drove the blackboard directly, not
voice/natural language; it does not exercise the Direct ability path fixed here.

The 14 journeys in `docs/micromachine-final-live-qa.md` (`parallel_scout_attack_defend` …
`voice_readback_callout_identity`) remain `manual_live_qa_remaining=true`.

### 6.3 First-session API-accuracy checklist

Capture for every item: game frame, the spoken/typed command, the Direct plan JSON
(`sc2.direct.*` result `audit.action_reports`), and the unit's `orders` before/after.

1. **Build pin.** Log `Base97364` binary path, and from a live `RequestData` response
   (`ability`/`unit_type`/`upgrade`) compare id↔name against `stableid.json` for the ~35 abilities in
   section 3. Any difference invalidates this audit for that build.
2. **Order fired.** After each micro command, read the caster's `orders[0].ability_id` (python-sc2:
   `unit.orders[0].ability.exact_id`) and assert it equals the id in section 3 (e.g. tank siege →
   388; stim → 380/253 via remap 3675). `queued`/`submission`/`published` is not evidence.
3. **State effect.** Tank → `SIEGETANKSIEGED` (32) within ~3 s; widow mine → `WIDOWMINEBURROWED` (500);
   Thor/Viking/Liberator/Hellion → `THORAP`(691)/`VIKINGASSAULT`(34)/`LIBERATORAG`(734)/`HELLIONTANK`(484).
4. **Buffs** (stableid `Buffs`, read from `unit.buffs`): stim → `Stimpack`(27) / `StimpackMarauder`(24);
   ghost cloak → `GhostCloak`(6); banshee cloak → `BansheeCloak`(7); Lock-On → `LockOn`(116).
   Medivac load → the medivac's `passengers`/cargo, not a buff. **Yamato has no `Buffs` row with
   "Yamato" in its name in this stableid** — confirm it by the caster's order `YAMATO_YAMATOGUN`(401)
   and the target's health drop instead. Ids above were read from this machine's `stableid.json`.
5. **Upgrades.** Research Stimpack/Combat Shield: tech-lab order id 730/731 present, minerals/gas
   debited, then `state.upgrades` contains `UpgradeId.STIMPACK`(15)/`SHIELDWALL`(16) on completion;
   confirm "Combat Shield" is really `SHIELDWALL` live (D7) and what happens for Yamato (D7 caveat:
   id 76 vs 258).
6. **Builds.** `TERRANBUILD_SUPPLYDEPOT` etc. issued to exactly one SCV; the structure appears
   (`structures`, `build_progress`); refinery targets the geyser **unit**; add-on/Orbital orders go to
   the idle parent (421/422/…, 1516) and the add-on tag appears in `add_on_tag`.
7. **Counts.** "탱크 시즈모드" with N tanks → N tanks sieged; "탱크 두 대 시즈" → exactly 2;
   "스팀팩" with marines+marauders → both stimmed; with only marauders → only them.
8. **Refusals.** Yamato/Snipe/EMP/Lock-On with no visible enemy must report
   `target_required: …`, not silently succeed; a Medivac with no damaged bio must refuse heal.
9. **Emergency.** retreat/hold/stop reach every combat unit (move 3794 point / 3793 / 3665 are
   remap ids; live ids may be 16/18/4 — record what the unit's `orders` show).
10. Record any mismatch as a new test in `tests/test_sc2_api_accuracy.py` before fixing it.

Telemetry to keep: `latest_telemetry.json`, `telemetry.jsonl`, `micromachine.log`,
`scenario_audit.json` per attempt (`live_qa_30_macos_local.sh` writes these), plus a
`GameLogs/` copy. All of this remains **unproven until executed on a machine with SC2.**
