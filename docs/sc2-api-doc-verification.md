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

Status legend: `verified-by-test` · `defect-fixed` · `handoff` · `cannot-verify-offline`. The
**product behaviour** and **status** columns are filled in §3 once the contract tests ran.

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
