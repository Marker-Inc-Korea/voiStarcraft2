# SC2 API documentation verification (offline, behaviour level)

**Scope and honesty.** StarCraft II is not installed here. Round 2 (`docs/sc2-api-accuracy.md`) verified
*ids and target kinds* against `stableid.json` + burnysc2. This document verifies *behaviour*: what the
OFFICIAL documentation says each API call/field does, versus what the product's **real lowering path**
(voice DSL -> `unified_command_router` -> `SC2RuntimeExecutor` -> `PythonSC2BotAdapter` -> real
python-sc2 `BotAI.do` -> `ActionRawUnitCommand` -> serialised `RequestAction`) actually sends.
Every "verified-by-test" row is a request-shape fact. **Nothing here proves a live game accepted an
order.** Section 5 is the checklist for the first live session.

## 1. Sources (all fetched/inspected offline-reproducibly)

| Short | What | Where |
|---|---|---|
| **AI** | `sc2::ActionInterface` class reference | <https://blizzard.github.io/s2client-api/classsc2_1_1_action_interface.html> (`UnitCommand` `#add138c6ebe9b1fcbeae393c03b7fdeb9`, `SendActions` `#af054d2a35e7eb740109f63011090ea50`, `Commands` `#a29a55f8dd4f9230864caa1deb228ae33`) |
| **AR** | `sc2::ActionRaw` | <https://blizzard.github.io/s2client-api/structsc2_1_1_action_raw.html> (`target_type` `#a6bd32cf44980cbaacc41fe56d338c3d7`, `unit_tags` `#ac8767f8740a584a60b39d27cd3df620c`) |
| **QI** | `sc2::QueryInterface` | <https://blizzard.github.io/s2client-api/classsc2_1_1_query_interface.html> (`GetAbilitiesForUnit` `#a76a602ebb27600b636712bd0bf062201`, `Placement` `#aa5ee0fbda6e0eac6f096710328484d2a`) |
| **AD** | `sc2::AbilityData` | <https://blizzard.github.io/s2client-api/structsc2_1_1_ability_data.html> (`target` `#a2351270150739e40d945e21fb801bedf`, `remaps_to_ability_id` `#a26ac48da5fd45e38e541250294438bb4`, `footprint_radius` `#a600be2fc1d258608994d9658f6488c8c`, `is_instant_placement` `#a9fe8873ca4353862d1e2425526628ec1`, `cast_range` `#ac70e65166f5a291b28da3c97c71833b4`) |
| **UT** | `sc2::UnitTypeData` | <https://blizzard.github.io/s2client-api/structsc2_1_1_unit_type_data.html> (`ability_id` `#a92a4833eee980a7ae26d85b216020944`, `food_required` `#a44f41fef59a8df77ae1e010b0ff1fc9c`, `tech_requirement` `#acd5714a07964d41b8af883d6b230e38e`, `require_attached` `#a71172b93c9f9ec00c82b81adce596d00`, `cargo_size` `#a13ab317cd91246aa40c928f48b801b38`) |
| **U** | `sc2::Unit` | <https://blizzard.github.io/s2client-api/classsc2_1_1_unit.html> (`orders` `#a45b97cf510454a385c372e512b40c51a`, `buffs` `#a34f17705c61114ad78c192ed7b06a9af`, `energy` `#aa2b194b8a5499b7591356dc6365c0cdc`, `weapon_cooldown` `#a09bcc532a373b225b4ddb16fa77e9c41`, `add_on_tag` `#a01b13cf74f1851983a834fdbf3e22b8a`, `passengers` `#a01e2249c0d1bd27d8b61f233fbd75b66`, `build_progress` `#abb3c46774c4d5dab4d641a4abcf68aba`, `is_flying` `#a6480f0b177f99a656ab3a273df3f8c3c`) |
| **UO** | `sc2::UnitOrder` | <https://blizzard.github.io/s2client-api/structsc2_1_1_unit_order.html> (`ability_id` `#a0582df20b81543fd031972a497920d74`, `progress` `#a820bb8f79dcaebcd02475b5ac2c522cc`) |
| **AA** | `sc2::AvailableAbilities` / `AvailableAbility` | <https://blizzard.github.io/s2client-api/structsc2_1_1_available_abilities.html>, <https://blizzard.github.io/s2client-api/structsc2_1_1_available_ability.html> |
| **AG** | `sc2::Agent` (client events `OnUnitIdle` …) | <https://blizzard.github.io/s2client-api/classsc2_1_1_agent.html> |
| **OI** | `sc2::ObservationInterface` | <https://blizzard.github.io/s2client-api/classsc2_1_1_observation_interface.html> |
| **T1/T2/T3** | Tutorials 1–3 | <https://blizzard.github.io/s2client-api/md_docs_tutorial1.html>, `…tutorial2.html` (idle SCV, SMART = right click), `…tutorial3.html` (Barracks, `ATTACK_ATTACK`) |
| **RAW** | `s2clientprotocol/raw.proto` (`ActionRawUnitCommand` L185-193, `UnitOrder` L52-59, `Unit` L113 `buff_ids`, L141-148 `orders`/`add_on_tag`/`passengers`/`weapon_cooldown`) | <https://github.com/Blizzard/s2client-proto/blob/master/s2clientprotocol/raw.proto> |
| **Q** | `s2clientprotocol/query.proto` (`ignore_resource_requirements` L13, `RequestQueryBuildingPlacement` L47-51) | <https://github.com/Blizzard/s2client-proto/blob/master/s2clientprotocol/query.proto> |
| **D** | `s2clientprotocol/data.proto` (`AbilityData` L12-37: `remaps_to_ability_id` L19, `Target` L21-27, `footprint_radius` L34, `is_instant_placement` L35; `UnitTypeData` `tech_requirement` L91 / `require_attached` L92) | <https://github.com/Blizzard/s2client-proto/blob/master/s2clientprotocol/data.proto> |
| **E** | `s2clientprotocol/error.proto` `ActionResult` (`CantQueueThatOrder`, `Cooldown`, `QueueIsFull`, `NotEnoughMinerals/Vespene/Food/Energy/Life`, `BuildTechRequirementsNotMet`, `CantBuildLocationInvalid`, `AddOnTooFarFromBuilding`, `CantLandLocationInvalid`, `NotEnoughRoomToLoadUnit`, `NoCargoToUnload`, `OrderQueueIsFull` …) | <https://github.com/Blizzard/s2client-proto/blob/master/s2clientprotocol/error.proto> |
| **P** | `docs/protocol/protocol.md` "Action Errors" (validation in `ResponseAction`, late failures in `ResponseObservation`) | <https://github.com/Blizzard/s2client-proto/blob/master/docs/protocol/protocol.md> |

Doc snapshots used: HTML pages saved from the live site on the audit day (`/tmp/s2docs/*.html`, 19 pages);
proto files from `Blizzard/s2client-proto` master `4867bb9` (2026-10-08). The Doxygen site is generated from
the 2019 API (s2client-api `614acc00`, see `docs/sc2-api-accuracy.md` §1).

Reproduce:

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_sc2_api_doc_contract.py tests/test_sc2_api_accuracy.py -q
```

Both skip (never fail) when `stableid.json` or burnysc2 is absent (`VOI_SC2_STABLEID=/path`).

## 2. Documented behaviours the user's voice depends on

Status legend: `verified-by-test` · `defect-fixed` · `handoff` · `cannot-verify-offline`. The row ids
(M/O/K/E) are resolved to product behaviour and status in §3.

### 2.1 Micro

| # | User says / needs | Documented behaviour | Doc source |
|---|---|---|---|
| M1 | any fresh order ("탱크 시즈") | `UnitCommand(..., queued_command=false)` is the default: the order **replaces** the unit's current orders. | AI `UnitCommand`; RAW L192 `queue_command` |
| M2 | "…다음에/이어서 …" (then) | `queued_command=true` / `queue_command=true` **appends** to the unit's order queue; invalid appends return `CantQueueThatOrder`/`OrderQueueIsFull`. | AI `UnitCommand`; RAW L192; E |
| M3 | ability that needs no target (stim, siege, cloak) | `ActionRaw::TargetNone` = "no target generally means 'self'". Target types are **mutually exclusive**. | AR `TargetType` |
| M4 | ability on one enemy (Yamato, Snipe, Lock-On, KD8) | `AbilityData::Target::Unit` → `target_unit_tag` is required; a point is not accepted. | AD `target`; D L21-27; RAW L187-190 |
| M5 | ability on ground (EMP, Nuke, Defender mode) | `AbilityData::Target::Point` → `target_world_space_pos`. | AD `target`; D L21-27 |
| M6 | command for many units at once | `UnitCommand(const Units&, …)`: "prefer this where possible" — one command carries all `unit_tags`. | AI `UnitCommand` (Units overloads) |
| M7 | "스팀팩" (generic) | `remaps_to_ability_id`: "this ability id may be represented by the given more generic id" — stim 380/253 → 3675. The order the unit reports may be the generic id. | AD `remaps_to_ability_id`; D L19 |
| M8 | stim | Availability is a **query**, not a guess: `GetAbilitiesForUnit` returns the abilities a unit can use now (energy/HP/cooldown/state). Stim needs HP > 10 and is refused by the game otherwise (`NotEnoughLife`) — game rule, only the *availability query* is documented. | QI `GetAbilitiesForUnit`; E `NotEnoughLife` |
| M9 | "시즈모드" / "시즈 풀어" | Form-specific abilities: `SIEGEMODE_SIEGEMODE` on `SIEGETANK`, `UNSIEGE_UNSIEGE` on `SIEGETANKSIEGED`; a unit not in that form does not list the ability. | AA `abilities`; UT `unit_alias` |
| M10 | "클로킹" | Energy is an observation: `Unit.energy`/`energy_max`; an order without energy fails `NotEnoughEnergy`. | U `energy`; E `NotEnoughEnergy` |
| M11 | "전술핵" | Needs a Ghost and a point (`Target::Point`); Nuke needs a Ghost Academy + armed Nuke (`BuildTechRequirementsNotMet`). | AD `target`; E |
| M12 | "스캐너" (Orbital) | Calldown Scanner Sweep is a **point** ability that costs 50 energy (`Unit.energy`); fails `NotEnoughEnergy`. | AD `target`; U `energy`; E |
| M13 | "메디박 태워/내려" | `LOAD_MEDIVAC` targets a **unit** (own cargo); `UNLOADALLAT_MEDIVAC` targets a **point**; passengers visible via `Unit.passengers`/`cargo_space_*`; `cargo_size` per unit type; `NotEnoughRoomToLoadUnit`, `NoCargoToUnload`, `CantUnloadUnitsThere`. | AD; U `passengers`; UT `cargo_size`; E |
| M14 | "메디박 치료" | `MEDIVACHEAL_HEAL` targets an own **unit** and requires a heal target (`RequiresHealTarget`). | AD `target`; E |
| M15 | cooldown abilities | `Unit.weapon_cooldown` = time remaining for a weapon; `ignore_resource_requirements` of `GetAbilitiesForUnit` "ignores food, mineral and gas costs, **as well as cooldowns**" (so default queries *include* them). | U `weapon_cooldown`; QI `GetAbilitiesForUnit`; Q L13 |
| M16 | unit already buffed (stim running) | Buffs are observable (`Unit.buffs` / `buff_ids`); a second stim while buffed is refused or ineffective. | U `buffs`; RAW L113 |
| M17 | "건물 띄워/착륙" (lift/land) | Land is a **point** ability; `Placement(ability, pos, placing_unit)` checks flying-building placement incl. add-on room; fails `CantLandLocationInvalid`, `AddOnTooFarFromBuilding`. | QI `Placement`; E |

### 2.2 Operation

| # | User says / needs | Documented behaviour | Doc source |
|---|---|---|---|
| O1 | "공격 가자" | `ATTACK_ATTACK` with a point (tutorial 3: `UnitCommand(unit, ABILITY_ID::ATTACK_ATTACK, point)`) = attack-move. | T3 "Scouting Your Opponent" |
| O2 | "이동/정찰" | `MOVE` with a point; no target = invalid (`Target::Point`). | AD `target` |
| O3 | right-click semantics | `SMART` "is equivalent to a right click in Starcraft 2 when you have a unit selected" (tutorial 2: send idle SCV to mineral). | T2 "Managing Idle SCVs" |
| O4 | "순찰" | `PATROL` is a point ability. | AD `target` |
| O5 | "마린 네 기" | An action addresses exactly the `unit_tags` it lists; the API has no "N of them" — the caller picks tags. | AR `unit_tags`; RAW L191 |
| O6 | "2기 정찰, 4기 공격" | One unit belongs to one order stream: re-issuing replaces; two operations over the same tags would overwrite each other (`queue_command=false`). | AI `UnitCommand`; RAW L192 |
| O7 | "SCV 수리" | Repair (`EFFECT_REPAIR`) targets an own **unit**; needs a damaged target (`RequiresRepairTarget`). | AD `target`; E |
| O8 | batched send | Commands are batched; in real-time they must be flushed (`SendActions`); `Commands()` lists tags that sent commands to check that the unit "actually has a command when the observation is received". | AI `SendActions`, `Commands`; AG `Actions` |
| O9 | order really fired | The truth is the next observation: `Unit.orders[].ability_id/target_pos/target_unit_tag/progress`. | U `orders`; UO |
| O10 | unit not able to be ordered | `YouCantControlThatUnit`, `UnitCantMove`, `CantTargetThatUnit`, `MustTargetEnemyUnits`; validation failures come back in `ResponseAction`, late failures in `ResponseObservation`. | E; P "Action Errors" |

### 2.3 Macro

| # | User says / needs | Documented behaviour | Doc source |
|---|---|---|---|
| K1 | "SCV 뽑아" | Training = no-target command on the **producer**: `UnitCommand(cc, TRAIN_SCV)`; `UnitTypeData.ability_id` = "which ability id creates the unit". Tutorial issues it from `OnUnitIdle`. | T2 "Building SCVs"; UT `ability_id` |
| K2 | idle vs busy producers | `OnUnitIdle`: called when a unit "has no orders or had orders in the previous step and currently does not". Queueing on a busy producer appends (queue limit → `QueueIsFull`). | T2; AG `OnUnitIdle`; E `QueueIsFull` |
| K3 | "병영 마린" needs a depot | `tech_requirement` "Structure required to build this unit"; `BuildTechRequirementsNotMet`. | UT `tech_requirement`; E |
| K4 | "공성전차" needs Tech Lab | `require_attached`: "whether tech_requirement is an add-on". The producer must have the add-on (`Unit.add_on_tag`). | UT `require_attached`; U `add_on_tag` |
| K5 | supply gating | `food_required` per unit; `ObservationInterface::GetFoodCap/GetFoodUsed`; `NotEnoughFood`. | UT `food_required`; OI; E |
| K6 | resource gating | `mineral_cost`/`vespene_cost`; `GetMinerals/GetVespene`; `NotEnoughMinerals/Vespene`. Validation fails immediately in `ResponseAction` ("didn't have enough resources"). | UT; OI; P |
| K7 | "서플라이 지어" | Structure ability has `is_building=true` (needs placement), is a **point** command to an **SCV** (tutorial 2 `TryBuildStructure`); `footprint_radius` = estimated footprint. | AD `is_building`, `footprint_radius`; T2 |
| K8 | "정제소" | Refinery build is unit-targeted at the geyser (`Target::Unit`); `MustBuildRefineryFirst` for gas gather. | AD `target`; E |
| K9 | placement check first | `QueryInterface::Placement(ability, pos, unit)` / `RequestQueryBuildingPlacement{ability_id,target_pos,placing_unit_tag}`: "whether a building can be placed at a location"; `ResponseQueryBuildingPlacement.result` is an `ActionResult`. | QI `Placement`; Q L47-55 |
| K10 | "애드온/리액터" | `is_instant_placement`: "placement next to an existing structure, e.g., an add-on like a Tech Lab"; target `PointOrNone`; no-target order **on the parent building**; `AddOnTooFarFromBuilding`. | AD `is_instant_placement`; D L26, L35; E |
| K11 | "연구" | Research is a no-target command on the research building (`UpgradeData.ability_id`); the building must be idle for a new research and exist (`TechRequirementsNotMet`). | `UpgradeData` (structsc2_1_1_upgrade_data.html); E |
| K12 | "궤도 사령부" | Town-hall morph `UPGRADETOORBITAL_ORBITALCOMMAND` is a no-target order on an idle CC. | AD; D L26 |
| K13 | construction progress | `Unit.build_progress` ∈ [0,1]; unfinished structures cannot train/research (`BuildingIsUnderConstruction`). | U `build_progress`; RAW L111; E |
| K14 | "확장" | `COMMANDCENTER` build is a point command at a free expansion (`CantBuildTooCloseToResources`). | AD; E |
| K15 | "일꾼 가스" | Gather = `HARVEST_GATHER` unit-target (SMART or gather on a refinery). | AD `target` |
| K16 | UI-free precondition | `ignore_resource_requirements=true` ⇒ availability is queried **without** cost/cooldown, so "can't afford right now" is distinguishable from "ability does not exist for this unit". | QI; Q L13 |

### 2.4 Emergency

| # | User says | Documented behaviour | Doc source |
|---|---|---|---|
| E1 | **후퇴** | Retreat is not an SC2 ability; the SC2 meaning is a `MOVE` (point target, `queue=false`) to a safe point for the army. | AD `target` (Move = Point); AI |
| E2 | **홀드** | `HOLDPOSITION` — no target, `queue=false`: unit stays and fights only what comes in range. | AD `target` (None); AI |
| E3 | **멈춰** | `STOP` — no target, `queue=false`: clears current orders (including queued ones). | AD `target` (None); AI |
| E4 | all combat units | One `UnitCommand(units, …)` with every tag (M6); the emergency must not leave stragglers. | AI `UnitCommand` (Units) |
| E5 | not queued | Emergency orders must replace, never append (`queue_command=false`), else they wait behind the current attack order. | RAW L192; AI |
| E6 | negation wins ("후퇴하지 마") | Pure product rule (not API); nothing documented. | — |
| E7 | workers | The API does not distinguish: any `unit_tags` gets the order. Whether SCVs are included is a *product choice* that must match what the user means by "army". | AR `unit_tags` |

## 3. Behaviour -> doc -> product -> status

Test names are in `tests/test_sc2_api_doc_contract.py` (`DC`) or `tests/test_sc2_api_accuracy.py` (`AC`, round 2).
"defect-fixed" = the DC test was written first, failed against `origin/main` (8ba4c43) and passes now
(commit `7ccf487` caster readiness, `39c796a` producer/gating/emergency).

### 3.1 Micro

| # | Product behaviour (what the real lowering emits) | Status | Evidence |
|---|---|---|---|
| M1 | Every voice command leaves with `queue_command=false` (micro, move/attack/smart/patrol, emergency) | verified-by-test | DC `test_a_fresh_voice_command_replaces_orders…`, `test_operation_commands_never_queue_either` |
| M2 | **"then / 다음에 / 이어서" cannot be expressed**: the DSL has no queue field (`realtime_intent.py:251 _MICRO_KEYS = {ability, unit_classes}`), and a payload carrying one is rejected loudly (not silently turned into replacing orders). The raw `queue_command=true` path exists in python-sc2 (`Unit.attack(queue=True)`) but nothing in the product sets it | **fixed in round 4** (§6.1) | VOCAB `test_spoken_then_words_reach_the_wire_as_queue_command` |
| M3 | no-target abilities (stim, siege, cloak, burrow, morphs) send no target; Unit/Point abilities send exactly the field their `AbilityData.Target` requires (all 33 tokens) | verified-by-test | DC `test_every_semantic_ability_sends_the_target_field…`; AC `test_voice_ability_lowers_to_the_exact_raw_command` (30 cases) |
| M4/M5 | Unit-target ability with no enemy visible = refusal `target_required: no_enemy_target`, never a point | verified-by-test | DC `test_unit_targeted_ability_without_a_target_is_a_visible_refusal`; AC `test_unit_target_ability_without_any_enemy…` |
| M6 | A group order is combined by python-sc2 into one command per ability when the ability is in `COMBINEABLE_ABILITIES` (STOP, HOLDPOSITION, ATTACK, burrow, lift…) and into one command per unit otherwise (siege, stim, morphs, and `MOVE_MOVE` id 16 — only the generic MOVE 3674 batches). Both are legal; only the bytes-on-the-wire count differs | verified-by-test | DC `test_the_three_emergency_words…` |
| M7 | Generic stim 3675 is sent for "스팀팩" (remaps from 380/253); live-reported order id may differ | verified-by-test (request) / cannot-verify-offline (observed order id) | AC voice cases; §5 item 2 |
| M8/M16 | **Stim needs HP above its cost and no existing stim buff.** Before: every selected marine/marauder was ordered (HP 10 marine, already-stimmed units included). Now: `health <= 10` (marine) / `<= 20` (marauder) and a `STIMPACK`(27) / `STIMPACKMARAUDER`(24) buff exclude the unit; nobody eligible = refusal `no_ready_caster: low_health_for_stim, already_stimmed` | **defect-fixed** | DC `test_stim_needs_hp_above_its_cost…`, `test_stim_with_nobody_eligible_is_a_visible_refusal` |
| M9 | Siege only on `SIEGETANK`, unsiege only on `SIEGETANKSIEGED` (form-specific ability lists) | verified-by-test | DC `test_siege_only_unsieged_tanks…`; AC `test_siege_unsiege_only_address_the_right_form` |
| M10 | **Cloak needs energy.** Before: a 5-energy Ghost/Banshee was ordered (`NotEnoughEnergy` in the game). Now only casters with observed `energy >= 25` are ordered; decloak needs none; unobserved energy never refuses | **defect-fixed** | DC `test_cloak_is_only_ordered_for_casters_that_have_the_energy[GHOST/BANSHEE]`, `test_decloak_needs_no_energy` |
| M11 | Nuke is a Ghost-only Point order; with no Ghost = refusal `no_matching_caster`. (Ghost Academy / armed-nuke prerequisite is a live-game rule: cannot-verify-offline) | verified-by-test / cannot-verify-offline | DC `test_tactical_nuke_is_a_ghost_only_point_order…` |
| M12 | **Scanner Sweep (Orbital, 50 energy), Calldown MULE, Supply Drop have no DSL token** — "스캐너" is rejected (`micro[0].ability must be one of the supported abilities`), not mis-executed | **fixed in round 4** (§6.2) | VOCAB `test_new_abilities_reach_the_wire…` |
| M13 | **Medivac load/unload.** Before: load was ordered on a full medivac and unload on an empty one. Now `cargo_left <= 0` blocks load (`NotEnoughRoomToLoadUnit`) and `cargo_used <= 0` blocks unload (`NoCargoToUnload`); load targets a unit, unload a point | **defect-fixed** | DC `test_medivac_load_skips_a_full_medivac…`, `test_medivac_unload_all_needs_cargo`; AC fixture now loads the medivac |
| M14 | Heal targets only a damaged **biological** unit (never a tank, never a full-HP marine) | verified-by-test | DC `test_medivac_heal_targets_a_damaged_biological_unit…` |
| M15 | Cooldown / `weapon_cooldown`: the product does not read it | cannot-verify-offline | §5 item 5 (`GetAbilitiesForUnit` availability) |
| M17 | **Lift / Land have no DSL token** (rejected loudly). Flying-building add-on room is therefore never checked | **fixed** (tokens, §6.2) / landing placement check still open (§6 item 5) | VOCAB |

### 3.2 Operation

| # | Product behaviour | Status | Evidence |
|---|---|---|---|
| O1 | Attack = `ATTACK` with `target_world_space_pos` (tutorial 3 uses `ATTACK_ATTACK`; python-sc2 sends the generic `ATTACK`) | verified-by-test | DC `test_attack_is_attack_attack_with_a_point…` |
| O2/O4 | move / patrol = point | verified-by-test | AC `test_group_orders_carry_a_world_point…` (16 cases) |
| O3 | SMART = right-click, point | verified-by-test | DC (same) |
| O5/O6 | "2기 정찰, 4기 공격" from the real compile path: 2 units get MOVE, 4 get ATTACK, 6 distinct tags, no unit gets two replacing orders | verified-by-test | DC `test_counted_operations_from_one_utterance…` |
| O7 | Repair with nothing damaged = refusal `no_damaged_repair_target` | verified-by-test | DC `test_repair_needs_a_damaged_target…`; AC `test_repair_is_a_unit_targeted_scv_order` |
| O8 | Orders are batched through python-sc2 `BotAI.do` and flushed by python-sc2 after the step (`_do_actions`); the product never calls `Commands()` to confirm | cannot-verify-offline | §5 |
| O9 | The product never reads `Unit.orders` back to prove an order fired (`submission/published` is not evidence — as `docs/sc2-api-accuracy.md` §6 says) | cannot-verify-offline | §5 item 1 |
| O10 | python-sc2 reads `ResponseAction`/`action_errors`, but **no product code consumes `ActionResult`** (`grep ActionResult|action_errors` in `starcraft_commander/` = 0 hits). A game-side rejection is therefore invisible to the user | **handoff** | owner: `micromachine_live_session.py` / `live_pipeline.py`; §5 item 8 |

### 3.3 Macro

| # | Product behaviour | Status | Evidence |
|---|---|---|---|
| K1 | Train = no-target command on the producer with the unit's creation ability (16 DSL units) | verified-by-test | AC `test_train_unit_uses_the_creation_ability…`, `test_every_trainable_dsl_unit…` |
| K2 | Only ready + idle producers of the right building are used; all-busy = refusal `no_ready_idle_producer` | verified-by-test | DC `test_train_uses_only_idle_ready_producers…`, `test_train_with_only_busy_producers…` |
| K4 | **Tech-lab units (Marauder, Ghost, Siege Tank, Cyclone, Thor, Banshee, Raven, Battlecruiser) were ordered from a naked Barracks/Factory/Starport.** Now only producers whose attached add-on is a Tech Lab are used; none = refusal `producer_needs_techlab`. (The doc's `tech_requirement`/`require_attached`; python-sc2 `requires_techlab`.) Ghost Academy / Armory / Fusion Core structure prerequisites are *not* checked (`tech_requirement` ids unknown without live `RequestData`) | **defect-fixed** (add-on) / cannot-verify-offline (structure prerequisite) | DC `test_units_that_need_a_tech_lab…` ×8; AC fixtures updated |
| K2b | **Queue depth.** Before: "마린 8기" on one Barracks sent 8 orders (`QueueIsFull`). Now 5 slots per producer (8 with Reactor), minus orders already queued; excess = partial report with detail `production_queue_full` | **defect-fixed** | DC `test_production_queue_is_capped…` |
| K5 | **Supply vs minerals.** Before: a supply block was reported as `unaffordable` ("자원이 부족합니다" — wrong reason). Now `supply_blocked` when `calculate_supply_cost > supply_left`; narrated (`narrator.py`) | **defect-fixed** | DC `test_supply_block_is_reported_as_supply…` |
| K6 | **Silent drop.** Before: a build/research/add-on the player could not afford returned `False`/ordered anyway; the executor produced `skipped` with **no reason**. Now build → refusal report `unaffordable`/`supply_blocked`; research and add-on/Orbital morph check `can_afford` first. Result: a visible `ActionRefused` error with Korean reason | **defect-fixed** | DC `test_a_build_the_player_cannot_afford…`, `test_research_is_gated_by_resources`, `test_addon_order_is_gated_by_resources` |
| K7 | Structures: point order on one SCV (`TERRANBUILD_*`) | verified-by-test | AC `test_build_structure_issues_a_point_targeted…` |
| K8 | Refinery = geyser unit | verified-by-test | AC `test_refinery_targets_a_geyser_unit…` |
| K9 | Placement rejection surfaces as `no_safe_placement: point(x,y): can_place_rejected…` (reason per candidate, Korean narration). The product does **not** use the documented `RequestQueryBuildingPlacement` (`ActionResult` reason) — it uses python-sc2 `can_place` | verified-by-test (surface) / cannot-verify-offline (reason fidelity vs `ActionResult`) | DC `test_placement_rejection_is_surfaced_with_a_reason` |
| K10 | **Add-on**: no-target order on an **idle, completed parent**; busy/unfinished parent = refusal; **a parent that already has an add-on is refused** (`barracks_already_has_addon`; before: a second add-on order was sent). Add-on *footprint free space* (`AddOnTooFarFromBuilding`, `is_instant_placement`) is **not** pre-checked (python-sc2 `Unit.add_on_position` exists; no map data here) | **defect-fixed** (one add-on) / cannot-verify-offline (footprint) | DC `test_addon_goes_on_an_idle_parent…`, `test_addon_is_not_ordered_on_a_barracks_that_already_has_one`; AC 8 add-on/morph shapes |
| K11 | Research goes to the idle Tech Lab/Engineering Bay, not a busy one; resources checked first | verified-by-test / defect-fixed (resources) | DC `test_research_goes_to_the_tech_lab_that_is_idle…`; AC research tests |
| K12 | Orbital/Planetary morph = no-target order on an idle CC; cost checked | verified-by-test / defect-fixed (cost) | AC; DC |
| K13 | `build_progress < 1` structures are never producers (`is_ready`) | verified-by-test | DC (train/add-on tests include a 0.4 / 0.5 progress structure) |
| K3 | Structure-tech prerequisites (Barracks needs a Depot, Ghost needs a Ghost Academy…) are **not checked by the adapter** (python-sc2 knows `TERRAN_TECH_REQUIREMENT`; `feasibility.py` pre-checks supply/workers only). The game answers `BuildTechRequirementsNotMet` which the product never reads (O10) | **fixed in round 4** (§6.3) | VOCAB `test_a_missing_prerequisite_structure_is_a_visible_refusal…` |

### 3.4 Emergency (the three words)

Determined from the REAL lowering (both the deterministic fast path `match_fast_path` and the model path
`tool_arguments_to_provider_output({"command_layer":"emergency",…})`):

| User says | Fast path flag | Router action | SC2 command emitted | Units | Status |
|---|---|---|---|---|---|
| **후퇴** (퇴각/철수/물러나) | `force_retreat` | `MOVE_GROUP "available combat units" target=self_main count=0` | `MOVE_MOVE` (16) with `target_world_space_pos` = own main, `queue=false` | every non-worker unit | verified-by-test |
| **홀드** (제자리/멈춰 서) | `hold_position` | `HOLD_POSITION … count=0` | `HOLDPOSITION` with no target, `queue=false` | every non-worker unit | verified-by-test |
| **멈춰** (정지/그만/공격 취소) | `cancel_attacks` | `STOP_GROUP … count=0` | `STOP` with no target, `queue=false` | every non-worker unit | verified-by-test |

* Model path "후퇴" = `{force_retreat, cancel_attacks}` -> `STOP` then `MOVE_MOVE` in one `RequestAction`, in that order, both non-queued; the final standing order is the move. Verified DC `test_model_path_retreat_stops_first_then_moves…`.
* **Workers are excluded** (`SC2_WORKER_TYPE_NAME`): "후퇴" does **not** evacuate SCVs and 멈춰 does not stop mining — this is the product's choice (the API itself would obey any tag); stated here because the user said "ALL army? workers excluded?". DC `test_emergency_words_address_every_combat_unit_but_neither_workers_nor_mules`.
* **Defect-fixed:** a MULE (a temporary worker), Auto-Turrets, Point-Defense Drones and Nukes were included in the "combat units" group, so "멈춰" would stop the MULE mining and "후퇴" would walk it home. They are now excluded (`_NON_ARMY_TYPE_NAMES`).
* Negation wins: `후퇴하지 마`, `홀드하지 마`, `멈추지 마`, `멈추지 말고 공격해` all abstain the fast path (DC `test_negation_wins_over_every_emergency_word`).
* **Known limitation (documented, not fixed):** python-sc2's ability table has no `MOVE` for sieged tanks / burrowed mines / AG liberators; a retreat batch carries their tags and the game may answer `UnitCantMove`. `STOP` is valid for sieged tanks and AG liberators. What SC2 actually does must be read live (§5 item 6). DC `test_emergency_still_orders_units_that_cannot_move` pins the limitation.

## 4. Defects found and fixed (all test-first)

| # | Defect | Fix location | Test (was red) |
|---|---|---|---|
| F1 | Stim ordered for HP <= cost and for already-stimmed units | `python_sc2_adapter.py _caster_blockers`, `_ability_casters`, `execute_ability` | `test_stim_needs_hp_above_its_cost…`, `test_stim_with_nobody_eligible…` |
| F2 | Cloak (and EMP/Snipe/Auto-Turret/Matrix/Anti-Armor) ordered without energy | same (`_ABILITY_ENERGY_COST`; only cloak is covered by a test, the other costs are **public rules not yet test-proven**) | `test_cloak_is_only_ordered…` |
| F3 | Medivac load on a full medivac / unload on an empty one | same | `test_medivac_load_skips_a_full_medivac…`, `test_medivac_unload_all_needs_cargo` |
| F4 | Tech-lab units trained from add-on-less producers | `train_unit`, `_producer_candidates` (`_unit_needs_techlab`) | `test_units_that_need_a_tech_lab…` |
| F5 | 8 marines queued on one Barracks (limit 5; 8 with Reactor) | `train_unit` (`_queue_slots_left`) | `test_production_queue_is_capped…` |
| F6 | Supply block narrated as "not enough resources" | `train_unit`/`build_structure`/`_morph_structure` (`_unaffordable_reason`), `narrator.py` | `test_supply_block_is_reported_as_supply…` |
| F7 | Unaffordable build/research/add-on dropped silently or ordered anyway | `build_structure`, `research_upgrade`, `_morph_structure` | `test_a_build_the_player_cannot_afford…`, `test_research_is_gated…`, `test_addon_order_is_gated…` |
| F8 | Second add-on ordered on a parent that already has one | `_morph_structure` | `test_addon_is_not_ordered_on_a_barracks_that_already_has_one` |
| F9 | MULE / Auto-Turret / drones / nukes obeyed 후퇴/홀드/멈춰 | `_select_group` (`_NON_ARMY_TYPE_NAMES`) | `test_emergency_words_address_every_combat_unit_but_neither_workers_nor_mules` |

Round-2 tests that encoded the old behaviour (`test_train_unit_uses_the_creation_ability…[MARAUDER|SIEGETANK]`,
`test_every_trainable_dsl_unit…` ×8, the medivac unload fixture) now provide a Tech Lab / cargo — the
assertions about the emitted request were not loosened.

## 5. What only a live SC2 can confirm (first-live-session checklist)

Read these fields from the observation (doc source in brackets) after each spoken command; any mismatch
becomes a new DC test before it is fixed.

1. **Order fired** — `unit.orders[].ability_id` (+ `target_world_space_pos` / `target_unit_tag`, `progress`) [U `orders`, UO]. Expect: siege 388; stim 380/253 (remapped from 3675 — record which one the game reports); STOP/HOLDPOSITION/MOVE ids as live `RequestData` says (generic 3665/3793/3674 vs 4/18/16).
2. **Ids** — `RequestData{ability_id,unit_type_id,upgrade_id,buff_id}` vs `stableid.json` for the ~35 abilities (AD `ability_id`, `remaps_to_ability_id`, `remaps_from_ability_id`).
3. **Availability** — `RequestQuery.abilities` / `GetAbilitiesForUnit(unit, ignore_resource_requirements=false)` for each caster just before an order: is the ability listed? Run twice (`true`/`false`) to separate cost/cooldown from "not offered" [QI, Q L13]. This is the authority for stim HP, cloak energy, siege form, nuke, scanner.
4. **Energy / HP / cooldown / buffs** — `unit.energy`, `unit.health`, `unit.weapon_cooldown`, `unit.buffs` (`buff_ids`) before/after: confirm the product's constants (stim HP 10/20, stim buffs 27/24, cloak 25, EMP 75, Snipe 50, Auto-Turret 50, Matrix 75, Anti-Armor 75) [U].
5. **State change** — `unit_type` of the caster after siege/unsiege/morph (SIEGETANKSIEGED 32, WIDOWMINEBURROWED 500, THORAP 691, VIKINGASSAULT 34, LIBERATORAG 734, HELLIONTANK 484), `is_flying`, `is_burrowed`, `cloak` [U].
6. **Emergency** — after "후퇴/홀드/멈춰": every army unit's `orders` (empty after STOP; HOLDPOSITION order after hold; MOVE with the home point after retreat). Record what sieged tanks / burrowed mines do with MOVE (`UnitCantMove`?) and that SCVs/MULEs were untouched.
7. **Production** — `unit.orders` of the producer (length ≤ 5, ≤ 8 with Reactor), `build_progress` of structures, `add_on_tag` (Tech Lab attached?), `passengers` / `cargo_space_taken` / `cargo_space_max` for the Medivac, `UnitTypeData.tech_requirement` + `require_attached` + `cargo_size` + `food_required` [UT].
8. **Rejections** — `ResponseAction.result[]` and `Observation.action_errors` (`ActionResult`: `Cooldown`, `NotEnoughEnergy`, `NotEnoughLife`, `QueueIsFull`, `CantQueueThatOrder`, `AddOnTooFarFromBuilding`, `CantBuildLocationInvalid`, `BuildTechRequirementsNotMet`, `UnitCantMove`…) [E, P]. The product does not read them today (handoff O10): until it does, a game-side rejection is only visible here.
9. **Placement** — `RequestQueryBuildingPlacement{ability_id,target_pos,placing_unit_tag}` -> `result` for a depot / Barracks + add-on footprint / flying-Barracks landing, compared with the product's `can_place` verdict [Q L47-55, AD `footprint_radius`, `is_instant_placement`].
10. **Queue** — two orders with `queue_command=true` (now emitted for "X 다음에 Y") -> `orders` has length 2 in order; also Scanner Sweep/MULE/Supply Drop: energy before/after (-50) and that a second scan within the cast animation is accepted or rejected; Lift/Land: `unit_type` flips BARRACKS <-> BARRACKSFLYING and `CantLandLocationInvalid` is seen for a bad spot.

## 6. Handoff items

Round 4 (`tests/test_sc2_vocab.py`, branch `feat/r4-vocab`) resolved items 1, 2, 4 and 6 of the round-3 list; items 3 and 5 are
still open. Status per item (offline, request-shape evidence only):

| # | Item | Status | Tests (`tests/test_sc2_vocab.py`) |
|---|---|---|---|
| 1 | **Queued commands** ("다음에 / 이어서 / 그 다음 / 끝나면 / and then / after that") | **fixed** | `test_spoken_then_words_reach_the_wire_as_queue_command` (12 utterances), `test_a_queued_follow_up_addresses_the_same_units…`, `test_a_non_queued_operation_pair_still_gets_disjoint_units`, `test_the_queue_flag_survives_the_vector_and_the_plan_serialisation`, `test_queue_must_be_a_boolean…`, `test_tool_schema_and_instructions_teach_the_queue_words`, `test_two_independent_builds_are_one_scv_with_the_second_queued`, `test_build_then_dependent_build_waits_for_the_prerequisite…`, `test_two_different_abilities_without_a_then_word_are_two_fresh_orders` |
| 2 | **Scanner Sweep / MULE / Supply Drop / Lift / Land** | **fixed** (Salvage, Load-into-bunker: **not done**, not trivial: Salvage is a one-way refund (`SalvageBunker` 1626/`SalvageShared` 32 are different ids per structure) and Load-into-Bunker needs a unit target and a free-slot count, neither observable offline) | `test_new_abilities_reach_the_wire…` (17 utterances), `test_scan_is_one_cast…`, `test_mule_goes_to_every_ready_orbital…`, `test_a_queued_scan_appends`, `test_infeasible_ability_is_refused_with_a_visible_korean_reason` (12 cases), `test_every_new_token_is_in_the_realtime_enum…`, `test_a_negated_new_ability_phrase_executes_nothing`, `test_the_fast_rule_cap_is_untouched` |
| 3 | **ActionResult never consumed** | **not fixed** (owner `live_pipeline.py` / `micromachine_live_session.py`, outside this branch) | — |
| 4 | **Structure tech prerequisites** | **fixed** (adapter pre-check; `feasibility.py` untouched) | `test_prerequisite_table_matches_burnysc2_and_stableid`, `test_a_missing_prerequisite_structure_is_a_visible_refusal…` (13 cases), `test_with_the_prerequisite_present_the_order_is_sent` (8), `test_a_tech_lab_unit_without_techlab…` |
| 5 | **Add-on footprint / landing placement** | **not fixed** (needs map data / `QueryInterface::Placement`; a Land order is sent without a placement check and the game may answer `CantLandLocationInvalid`/`AddOnTooFarFromBuilding`) | — |
| 6 | **Banshee cloak marker** | **fixed**, see below | `test_banshee_cloak_ids_are_the_unit_specific_ones_in_stableid`, `test_the_evidence_matcher_accepts_the_banshee_specific_marker` (7) |

### 6.1 Queueing design (item 1)

* Field: `queue: bool` on `micro[]` items, `operations[]` items and `macro.building_tasks[]` items of the realtime tool; carried as
  `TacticalTaskModulation.queue` / `BuildingTask.queue` -> `SC2CommandAction.queue` (omitted from `to_dict()` when false, so every
  non-queued payload is byte-identical to before) -> `PythonSC2BotAdapter._issue_unit_order(..., queue=True)` -> python-sc2
  `Unit.<order>(…, queue=True)` -> `ActionRawUnitCommand.queue_command = true` (raw.proto L192, AI `UnitCommand(queued_command)`).
* Rule: the model sets `queue:true` on every step AFTER the first one of a chain it heard joined by the then-words; the prompt (rule 15)
  and the tool schema say so. A fresh order carries no flag and keeps `queue_command=false` (replaces orders). The flag is a strict boolean;
  `"yes"` is rejected loudly.
* Units: a queued step continues the SAME units/SCV as the step it follows (`direct_binding._queued_predecessor`), otherwise
  "정찰 갔다가 돌아와" would order a second, different squad. Un-queued operations keep disjoint units.
* `execute_ability` inside `operations[]` was refused by the live operation director. Abilities listed together in `micro[]` are now marked
  as micro-layer operations (Direct-only) so "시즈하고 스팀팩" (no then-word) is two simultaneous fresh orders instead of a refusal; an
  unmarked ability inside `operations[]` is still refused (test `test_an_unmarked_ability_inside_operations_is_still_refused`).
* Builds: a queued build whose prerequisite is the structure the earlier step is still building ("배럭 짓고 다음에 벙커") is **not**
  shift-queued (the game would answer `BuildTechRequirementsNotMet` while the Barracks does not exist); it becomes a dependent plan
  released on `building_completed`, like build -> train. A queued build with no such dependency is one shift-queued SCV order.
* Not done: queueing for train/research/emergency actions is accepted by the data model but only micro, move/attack/patrol/smart and
  build lowerings are exercised by tests. A realtime *macro* call is published to MicroMachine (which consumes only `building_tasks.0`);
  the Direct build path is reached by operation-layer vectors (text LLM lane / MCP), which is where the tests drive it.

### 6.2 New abilities (item 2): ids and semantics used

| Token (Korean aliases in the prompt) | Ability (stableid id) | Target kind (header) | Caster | Readiness / refusal |
|---|---|---|---|---|
| `scanner_sweep` (스캐너, 스캔) | `SCANNERSWEEP_SCAN` 399 | Point | Orbital Command | energy >= 50 (`Unit.energy`); needs a spoken `location` (else `target_required: explicit_point_required`); no Orbital = `no_orbital_command`; one cast, the Orbital with most energy |
| `calldown_mule` (뮤마, 자원 채취) | `CALLDOWNMULE_CALLDOWNMULE` 171 | Unit,Point -> a mineral patch | Orbital Command | energy >= 50; every ready Orbital, each on a different patch; no patch = `no_mineral_patch` |
| `supply_drop` (보급 드롭) | `SUPPLYDROP_SUPPLYDROP` 255 | Unit -> a finished Supply Depot | Orbital Command | energy >= 50; one cast; no depot = `no_supply_depot` |
| `lift_off` (이륙, 띄워) | `LIFT_BARRACKS` 452 / `LIFT_FACTORY` 485 / `LIFT_STARPORT` 518 / `LIFT_COMMANDCENTER` 417 / `LIFT_ORBITALCOMMAND` 1522 (remap to `LIFT` 3679) | None | the named structure | finished and idle (`structure_busy`, `under_construction`) |
| `land` (착륙, 내려안아) | `LAND_BARRACKS` 554 / `LAND_FACTORY` 520 / `LAND_STARPORT` 522 / `LAND_COMMANDCENTER` 419 / `LAND_ORBITALCOMMAND` 1524 (remap to `LAND` 3678) | Point | the flying structure | needs `location`; placement is NOT checked (item 5) |

The 50-energy cost, "one scan per command" and the depot/patch targets are public game rules, not in stableid.json: they are on the live checklist (§5 items 3-4).
The tokens live in `MICROMACHINE_DIRECT_ONLY_ABILITIES` (never in `MICROMACHINE_TACTICAL_ABILITIES`, which is the C++ patch contract), so MicroMachine and
the pre-live journey evidence do not see them. They are deliberately **not** fast-rule families (cap 5 unchanged; "스캔" alone abstains).

### 6.3 Structure prerequisites (item 4)

`starcraft_commander/terran_prerequisites.py` holds the table (Barracks<-Supply Depot; Factory, Bunker, Ghost Academy, Orbital<-Barracks;
Starport, Armory<-Factory; Fusion Core<-Starport; Ghost<-Ghost Academy; Thor<-Armory; Battlecruiser<-Fusion Core; Planetary Fortress,
Turret, Sensor Tower<-Engineering Bay). `test_prerequisite_table_matches_burnysc2_and_stableid` compares it with burnysc2
`TERRAN_TECH_REQUIREMENT`; **no mismatch was found** (the doc's `UnitTypeData.tech_requirement` ids themselves need live `RequestData`; burnysc2's table is the
offline authority). Lowered Depots and flying Barracks/Factory/Starport count; a structure with `build_progress < 1` does not (`prerequisite_under_construction`).
Refusal text is Korean ("선행 건물이 없습니다 — 병영이(가) 먼저 필요합니다"). The check lives in `PythonSC2BotAdapter._missing_prerequisite` and covers
`build_structure`, add-on/morph (`Orbital`, `Planetary`) and `train_unit`; **research prerequisites are not checked** (no research-tech table here).

### 6.4 Banshee cloak marker (item 6)

`micromachine_command_execution.py` expected the generic `BEHAVIOR_CLOAKON` marker while patch 0047 (and the Direct adapter) use the Banshee-specific id
392 (`BEHAVIOR_CLOAKOFF_BANSHEE` 393). The marker table now lists the specific id first and keeps the generic one. Impact on journey evidence: see §7
(journey bundles compared member by member).

## 7. Journey-evidence check (pre-live provenance)

This branch changes `python_sc2_adapter.py` and `narrator.py` (not the router, executor or live session).
Journey bundles were built with the CI binary (`binary_sha256 b7f911ab…0dd8`, status `accepted`, 0 blockers) from
pristine `8ba4c43` and from this branch with `python -m starcraft_commander.micromachine_pre_live_journeys
--emit-bundle`: **32 members in each, 0 added/removed, 0 differing (SHA-256 per member)**; the two zips are also
the same size (16231596 bytes). Machine load during the run was ~180 (only the byte comparison matters, no timing).
