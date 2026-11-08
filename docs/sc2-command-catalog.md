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

## Design notes

* **Source of truth.** `scripts/sc2_accuracy/gen_catalog_facts.py` derives one fact row per distinct non-generic Terran
  command from the s2client-api header extract (`unit_abilities`, `abilities`: id and target kind) and burnysc2
  (`UNIT_ABILITIES`, `GENERIC_REDIRECT_ABILITIES`, `RESEARCH_INFO`, `TRAIN_INFO`): issuer unit types, the exact
  `AbilityId` per issuer, upgrade levels and their building/previous-level requirements, tech-lab/tech requirements.
  Generic movement/targeting abilities (`ATTACK MOVE STOP PATROL HOLDPOSITION SMART CANCEL RALLY_* HARVEST_* HALT`) are not
  catalog commands. 103 = train 17, build 16, research 24, morph 14, effect 20, behavior 4, other 8.
* **Race independence.** The data module is keyed by `TERRAN_` rows of the header; the entry type, lookup API and
  evaluator carry no Terran literal. Protoss/Zerg are added with a fact row source plus an annotation row (aliases, effect
  text, token) in `sc2_catalog.py` and cues in `sc2_catalog_cues.py`.
* **Drift guard.** `tests/test_sc2_catalog.py::test_catalog_has_one_entry_per_header_command...` compares the catalog with the
  header and fails when the data gains a command; `test_generated_facts_are_fresh` fails when `sc2_catalog_data.py` no
  longer equals a fresh generation; importing `sc2_catalog` raises when a fact row has no annotation.
* **Intent, not ability names.** `search()` ranks exact alias, then plain-language cues (`sc2_catalog_cues.py`), then an
  alias inside the phrase. A cue is a conjunction of alternatives (`밴시+은신|투명+연구`) with entry-level vetoes, so
  `밴시 은신 연구` selects the research and not the cloak behaviour. Several results are returned on ties on purpose.
  Intent-corpus rows (`scripts/bench/intent_corpus.jsonl`, the 57 `int-*` rows): top-1 57/57 and top-3 57/57 acceptable
  commands, but the cues were written while looking at those rows, so this number is NOT held-out. The held-out set in the test
  (20 phrases written after the cues): top-1 19/20, top-3 20/20 after one cue fix for the user's own example
  `탱크 사거리 늘려줘` (tank range is siege mode; there is no tank range research in the API).
* **Multi-level research.** `research_terraninfantryweapons/armor`, `research_terranvehicleweapons`,
  `research_terranvehicleandshipplating`, `research_terranshipweapons` each carry three upgrade levels. `evaluate()` chooses
  the next level from `upgrades_done`/`upgrades_pending`; level 2/3 need an Armory (burnysc2 `required_building`) and the previous
  level. The catalog never guesses a level the game would refuse.
* **Direct path vs MicroMachine.** No catalog token is added to `MICROMACHINE_TACTICAL_ABILITIES` (the C++ contract). New
  voice tokens (`ghost_hold_fire`, `ghost_release_fire`, `arm_nuke`, `hunter_seeker_missile`, `point_defense_drone`,
  `bunker_salvage`, `spray`, `bunker_stimpack`, `widow_mine_attack`, `bunker_load`, `command_center_load_all`,
  `command_center_unload_all`, `bunker_unload_all`, `supply_depot_lower`, `supply_depot_raise`) live in
  `MICROMACHINE_DIRECT_ONLY_ABILITIES`, which the compiler accepts but MicroMachine never receives.
* **No new SC2ActionType.** Research lowers to `RESEARCH_UPGRADE`, add-ons/orbital/planetary to `BUILD_STRUCTURE`,
  trains to `TRAIN_UNIT`, SCV buildings to `BUILD_STRUCTURE`, repair to `REPAIR`, everything else to `EXECUTE_ABILITY`.
* **Data caveats found while generating.** burnysc2 4.11.4 has no research row for 7 Terran research abilities
  (`NeosteelFrame`, `CycloneRapidFireLaunchers`, `RavenCorvidReactor`, `RavenRecalibratedExplosives`,
  `RavenEnhancedMunitions`, `AdvancedBallistics`, `HighCapacityFuelTanks`); the upgrade ids come from stableid.json names
  (`EXTRA_UPGRADES` in the generator) and `HighCapacityFuelTanks` has NO upgrade id in stableid.json, so it cannot be
  tracked as done/pending. burnysc2 lists `SALVAGEEFFECT_SALVAGE` (4128) for the Bunker, but stableid.json names id 4128
  `MothershipCloak`, so the catalog sends the header-documented generic `EFFECT_SALVAGE` (32, Target None): *generic id;
  live remap unconfirmed*. 13 (command, issuer) rows are flagged as
  `not_in_python_sc2_unit_abilities` (header says the unit has it; burnysc2's per-unit table does not).

## What only a live SC2 can confirm

* That the game accepts each order in a real state (the tables prove request SHAPE: ability id, target kind, unit tags).
* The energy costs and queue/addon rules are public game rules, not in stableid.json; re-verify with `Unit.energy` and
  `QueryInterface::GetAbilitiesForUnit`.
* Whether `UnitTypeData.tech_requirement`/`require_attached` match the current patch (burnysc2 4.11.4 / header 2019-08).
* Ids newer than the 2019 header (interference matrix 3747, anti-armor missile 3753, Hyperjump) have an inferred target kind.
* Whether `BUILD_NUKE` needs an idle Ghost Academy silo and whether `EFFECT_NUKECALLDOWN` is accepted for the generic id.
* Whether multi-level research is accepted while a lower level of ANOTHER family is researching.

## Command table

<!-- BEGIN GENERATED TABLE -->

| # | id | category | issuer -> exact ability | target | needs | Korean intent (alias) | offline status |
|--:|----|----------|------------------------|--------|-------|-----------------------|----------------|
| 1 | `behavior_cloakoff` | behavior | BANSHEE -> BEHAVIOR_CLOAKOFF_BANSHEE<br>GHOST -> BEHAVIOR_CLOAKOFF_GHOST | none | - | 클로킹 풀어 | BANSHEE: sent<br>GHOST: sent |
| 2 | `behavior_cloakon` | behavior | BANSHEE -> BEHAVIOR_CLOAKON_BANSHEE<br>GHOST -> BEHAVIOR_CLOAKON_GHOST | none | energy 25 | 클로킹 | BANSHEE: sent<br>GHOST: sent |
| 3 | `behavior_holdfireoff` | behavior | GHOST -> BEHAVIOR_HOLDFIREOFF_GHOST | none | - | 사격 재개 | GHOST: refused: unresolvable_ability |
| 4 | `behavior_holdfireon` | behavior | GHOST -> BEHAVIOR_HOLDFIREON_GHOST | none | - | 사격 중지 | GHOST: refused: unresolvable_ability |
| 5 | `build_armory` | build | SCV -> TERRANBUILD_ARMORY | point | 군수공장 | 무기고 지어 | SCV: sent |
| 6 | `build_barracks` | build | SCV -> TERRANBUILD_BARRACKS | point | 보급고 | 병영 지어 | SCV: sent |
| 7 | `build_bunker` | build | SCV -> TERRANBUILD_BUNKER | point | 병영 | 벙커 지어 | SCV: sent |
| 8 | `build_commandcenter` | build | SCV -> TERRANBUILD_COMMANDCENTER | point | - | 사령부 지어 | SCV: sent |
| 9 | `build_engineeringbay` | build | SCV -> TERRANBUILD_ENGINEERINGBAY | point | 사령부 | 공학 연구소 지어 | SCV: sent |
| 10 | `build_factory` | build | SCV -> TERRANBUILD_FACTORY | point | 병영 | 군수공장 지어 | SCV: sent |
| 11 | `build_fusioncore` | build | SCV -> TERRANBUILD_FUSIONCORE | point | 우주공항 | 융합로 지어 | SCV: sent |
| 12 | `build_ghostacademy` | build | SCV -> TERRANBUILD_GHOSTACADEMY | point | 병영 | 유령 사관학교 지어 | SCV: sent |
| 13 | `build_missileturret` | build | SCV -> TERRANBUILD_MISSILETURRET | point | 공학 연구소 | 미사일 터렛 지어 | SCV: sent |
| 14 | `build_nuke` | build | GHOSTACADEMY -> BUILD_NUKE | none | - | 핵 장전 | GHOSTACADEMY: refused: unresolvable_ability |
| 15 | `build_reactor` | build | BARRACKS -> BUILD_REACTOR_BARRACKS<br>FACTORY -> BUILD_REACTOR_FACTORY<br>STARPORT -> BUILD_REACTOR_STARPORT | none | - | 리액터 달아 | BARRACKS: sent<br>FACTORY: sent<br>STARPORT: sent |
| 16 | `build_refinery` | build | SCV -> TERRANBUILD_REFINERY | unit (가스 간헐천) | - | 가스 지어 | SCV: sent |
| 17 | `build_sensortower` | build | SCV -> TERRANBUILD_SENSORTOWER | point | 공학 연구소 | 감시탑 지어 | SCV: sent |
| 18 | `build_starport` | build | SCV -> TERRANBUILD_STARPORT | point | 군수공장 | 우주공항 지어 | SCV: sent |
| 19 | `build_supplydepot` | build | SCV -> TERRANBUILD_SUPPLYDEPOT | point | - | 보급고 지어 | SCV: sent |
| 20 | `build_techlab` | build | BARRACKS -> BUILD_TECHLAB_BARRACKS<br>FACTORY -> BUILD_TECHLAB_FACTORY<br>STARPORT -> BUILD_TECHLAB_STARPORT | none | - | 기술실 달아 | BARRACKS: sent<br>FACTORY: sent<br>STARPORT: sent |
| 21 | `burrowdown` | other | WIDOWMINE -> BURROWDOWN_WIDOWMINE | none | - | 지뢰 매복 | WIDOWMINE: sent |
| 22 | `burrowup` | other | WIDOWMINEBURROWED -> BURROWUP_WIDOWMINE | none | - | 지뢰 매복 풀어 | WIDOWMINEBURROWED: sent |
| 23 | `effect_autoturret` | effect | RAVEN -> BUILDAUTOTURRET_AUTOTURRET | point (설치 위치) | energy 50 | 자동 터렛 | RAVEN: sent |
| 24 | `effect_calldownmule` | effect | ORBITALCOMMAND -> CALLDOWNMULE_CALLDOWNMULE | point_or_unit (미네랄 광맥) | energy 50 | 뮬 | ORBITALCOMMAND: sent |
| 25 | `effect_emp` | effect | GHOST -> EMP_EMP | point (적 위치) | energy 75 | EMP | GHOST: sent |
| 26 | `effect_ghostsnipe` | effect | GHOST -> EFFECT_GHOSTSNIPE | unit (적 유닛) | energy 50 | 저격 | GHOST: sent |
| 27 | `effect_heal` | effect | MEDIVAC -> MEDIVACHEAL_HEAL | unit (부상 아군 생체 유닛) | - | 치료 | MEDIVAC: sent |
| 28 | `effect_hunterseekermissile` | effect | RAVEN -> SEEKERMISSILE_HUNTERSEEKERMISSILE | unit (적 유닛) | - | 추적 미사일 | RAVEN: refused: unresolvable_ability |
| 29 | `effect_kd8charge` | effect | REAPER -> KD8CHARGE_KD8CHARGE | point_or_unit (적 유닛/위치) | - | KD8 | REAPER: sent |
| 30 | `effect_lockon` | effect | CYCLONE -> LOCKON_LOCKON | unit (적 유닛) | - | 락온 | CYCLONE: sent |
| 31 | `effect_medivacigniteafterburners` | effect | MEDIVAC -> EFFECT_MEDIVACIGNITEAFTERBURNERS | none | - | 부스터 | MEDIVAC: sent |
| 32 | `effect_nukecalldown` | effect | GHOST -> TACNUKESTRIKE_NUKECALLDOWN | point (핵 투하 위치) | - | 핵 발사 | GHOST: sent |
| 33 | `effect_pointdefensedrone` | effect | RAVEN -> PLACEPOINTDEFENSEDRONE_POINTDEFENSEDRONE | point (설치 위치) | - | 방어 드론 | RAVEN: refused: unresolvable_ability |
| 34 | `effect_repair` | effect | MULE -> EFFECT_REPAIR_MULE<br>SCV -> EFFECT_REPAIR_SCV | unit (손상된 아군 대상) | - | 수리 | MULE: sent<br>SCV: sent |
| 35 | `effect_salvage` | effect | BUNKER -> SALVAGEEFFECT_SALVAGE | none | - | 벙커 해체 | BUNKER: refused: unresolvable_ability |
| 36 | `effect_scan` | effect | ORBITALCOMMAND -> SCANNERSWEEP_SCAN | point (스캔 위치) | energy 50 | 스캔 | ORBITALCOMMAND: sent |
| 37 | `effect_spray` | effect | SCV -> EFFECT_SPRAY_TERRAN | point (스프레이 위치) | - | 스프레이 | SCV: refused: unresolvable_ability |
| 38 | `effect_stim` | effect | BUNKER -> EFFECT_STIM<br>MARAUDER -> EFFECT_STIM_MARAUDER<br>MARINE -> EFFECT_STIM_MARINE | none | - | 스팀팩 | BUNKER: refused: unresolvable_ability<br>MARAUDER: sent<br>MARINE: sent |
| 39 | `effect_supplydrop` | effect | ORBITALCOMMAND -> SUPPLYDROP_SUPPLYDROP | unit (보급고) | energy 50 | 보급 드롭 | ORBITALCOMMAND: sent |
| 40 | `effect_tacticaljump` | effect | BATTLECRUISER -> EFFECT_TACTICALJUMP | point (도약 위치) | - | 전술 도약 | BATTLECRUISER: sent |
| 41 | `effect_widowmineattack` | effect | WIDOWMINEBURROWED -> WIDOWMINEATTACK_WIDOWMINEATTACK | unit (적 유닛) | - | 지뢰 발사 | WIDOWMINEBURROWED: refused: unresolvable_ability |
| 42 | `effect_yamatogun` | effect | BATTLECRUISER -> YAMATO_YAMATOGUN | unit (적 유닛) | - | 야마토 | BATTLECRUISER: sent |
| 43 | `land` | other | BARRACKSFLYING -> LAND_BARRACKS<br>COMMANDCENTERFLYING -> LAND_COMMANDCENTER<br>FACTORYFLYING -> LAND_FACTORY<br>ORBITALCOMMANDFLYING -> LAND_ORBITALCOMMAND<br>STARPORTFLYING -> LAND_STARPORT | point (착륙 위치) | - | 착륙 | BARRACKSFLYING: sent<br>COMMANDCENTERFLYING: sent<br>FACTORYFLYING: sent<br>ORBITALCOMMANDFLYING: sent<br>STARPORTFLYING: sent |
| 44 | `lift` | other | BARRACKS -> LIFT_BARRACKS<br>COMMANDCENTER -> LIFT_COMMANDCENTER<br>FACTORY -> LIFT_FACTORY<br>ORBITALCOMMAND -> LIFT_ORBITALCOMMAND<br>STARPORT -> LIFT_STARPORT | none | - | 이륙 | BARRACKS: sent<br>COMMANDCENTER: sent<br>FACTORY: sent<br>ORBITALCOMMAND: sent<br>STARPORT: sent |
| 45 | `load` | other | BUNKER -> LOAD_BUNKER<br>MEDIVAC -> LOAD_MEDIVAC | unit (태울 아군 유닛) | - | 태워 | BUNKER: refused: unresolvable_ability<br>MEDIVAC: sent |
| 46 | `loadall` | other | COMMANDCENTER -> LOADALL_COMMANDCENTER<br>COMMANDCENTERFLYING -> LOADALL_COMMANDCENTER<br>PLANETARYFORTRESS -> LOADALL_COMMANDCENTER | none | - | 사령부에 일꾼 넣어 | COMMANDCENTER: refused: unresolvable_ability<br>COMMANDCENTERFLYING: refused: unresolvable_ability<br>PLANETARYFORTRESS: refused: unresolvable_ability |
| 47 | `morph_hellbat` | morph | HELLION -> MORPH_HELLBAT | none | - | 화염기갑병으로 | HELLION: sent |
| 48 | `morph_hellion` | morph | HELLIONTANK -> MORPH_HELLION | none | - | 화염차로 | HELLIONTANK: sent |
| 49 | `morph_liberatoraamode` | morph | LIBERATORAG -> MORPH_LIBERATORAAMODE | none | - | 해방선 전투기 모드 | LIBERATORAG: sent |
| 50 | `morph_liberatoragmode` | morph | LIBERATOR -> MORPH_LIBERATORAGMODE | point (방어 구역 위치) | - | 해방선 방어 모드 | LIBERATOR: sent |
| 51 | `morph_orbitalcommand` | morph | COMMANDCENTER -> UPGRADETOORBITAL_ORBITALCOMMAND | none | 병영 | 궤도 사령부 | COMMANDCENTER: sent |
| 52 | `morph_planetaryfortress` | morph | COMMANDCENTER -> UPGRADETOPLANETARYFORTRESS_PLANETARYFORTRESS | none | 공학 연구소 | 행성 요새 | COMMANDCENTER: sent |
| 53 | `morph_siegemode` | morph | SIEGETANK -> SIEGEMODE_SIEGEMODE | none | - | 시즈 모드 | SIEGETANK: sent |
| 54 | `morph_supplydepot_lower` | morph | SUPPLYDEPOT -> MORPH_SUPPLYDEPOT_LOWER | none | - | 보급고 내려 | SUPPLYDEPOT: refused: unresolvable_ability |
| 55 | `morph_supplydepot_raise` | morph | SUPPLYDEPOTLOWERED -> MORPH_SUPPLYDEPOT_RAISE | none | - | 보급고 올려 | SUPPLYDEPOTLOWERED: refused: unresolvable_ability |
| 56 | `morph_thorexplosivemode` | morph | THORAP -> MORPH_THOREXPLOSIVEMODE | none | - | 토르 폭발 모드 | THORAP: sent |
| 57 | `morph_thorhighimpactmode` | morph | THOR -> MORPH_THORHIGHIMPACTMODE | none | - | 토르 고충격 모드 | THOR: sent |
| 58 | `morph_unsiege` | morph | SIEGETANKSIEGED -> UNSIEGE_UNSIEGE | none | - | 시즈 풀어 | SIEGETANKSIEGED: sent |
| 59 | `morph_vikingassaultmode` | morph | VIKINGFIGHTER -> MORPH_VIKINGASSAULTMODE | none | - | 바이킹 착륙 | VIKINGFIGHTER: sent |
| 60 | `morph_vikingfightermode` | morph | VIKINGASSAULT -> MORPH_VIKINGFIGHTERMODE | none | - | 바이킹 전투기 모드 | VIKINGASSAULT: sent |
| 61 | `research_advancedballistics` | research | STARPORTTECHLAB -> RESEARCH_ADVANCEDBALLISTICS | none | - | 고급 탄도학 | STARPORTTECHLAB: refused: unresolvable_upgrade |
| 62 | `research_bansheecloakingfield` | research | STARPORTTECHLAB -> RESEARCH_BANSHEECLOAKINGFIELD | none | - | 밴시 은신 연구 | STARPORTTECHLAB: refused: unresolvable_upgrade |
| 63 | `research_bansheehyperflightrotors` | research | STARPORTTECHLAB -> RESEARCH_BANSHEEHYPERFLIGHTROTORS | none | - | 밴시 속도 연구 | STARPORTTECHLAB: refused: unresolvable_upgrade |
| 64 | `research_battlecruiserweaponrefit` | research | FUSIONCORE -> RESEARCH_BATTLECRUISERWEAPONREFIT | none | - | 야마토 연구 | FUSIONCORE: refused: unresolvable_upgrade |
| 65 | `research_combatshield` | research | BARRACKSTECHLAB -> RESEARCH_COMBATSHIELD | none | - | 전투 방패 연구 | BARRACKSTECHLAB: sent |
| 66 | `research_concussiveshells` | research | BARRACKSTECHLAB -> RESEARCH_CONCUSSIVESHELLS | none | - | 충격탄 연구 | BARRACKSTECHLAB: refused: unresolvable_upgrade |
| 67 | `research_drillingclaws` | research | FACTORYTECHLAB -> RESEARCH_DRILLINGCLAWS | none | level>1: 무기고 + previous level | 드릴 발톱 연구 | FACTORYTECHLAB: refused: unresolvable_upgrade |
| 68 | `research_enhancedmunitions` | research | STARPORTTECHLAB -> STARPORTTECHLABRESEARCH_RAVENRESEARCHENHANCEDMUNITIONS | none | - | 레이븐 강화 탄약 | STARPORTTECHLAB: refused: unresolvable_upgrade |
| 69 | `research_highcapacityfueltanks` | research | STARPORTTECHLAB -> RESEARCH_HIGHCAPACITYFUELTANKS | none | - | 고용량 연료 탱크 | STARPORTTECHLAB: refused: unresolvable_upgrade |
| 70 | `research_hisecautotracking` | research | ENGINEERINGBAY -> RESEARCH_HISECAUTOTRACKING | none | - | 고보안 자동 추적 | ENGINEERINGBAY: sent |
| 71 | `research_infernalpreigniter` | research | FACTORYTECHLAB -> RESEARCH_INFERNALPREIGNITER | none | - | 화염 예열기 연구 | FACTORYTECHLAB: refused: unresolvable_upgrade |
| 72 | `research_neosteelframe` | research | ENGINEERINGBAY -> RESEARCH_NEOSTEELFRAME | none | - | 네오스틸 프레임 | ENGINEERINGBAY: refused: insufficient_researchers |
| 73 | `research_personalcloaking` | research | GHOSTACADEMY -> RESEARCH_PERSONALCLOAKING | none | - | 유령 은신 연구 | GHOSTACADEMY: sent |
| 74 | `research_rapidfirelaunchers` | research | FACTORYTECHLAB -> RESEARCH_CYCLONERAPIDFIRELAUNCHERS | none | - | 사이클론 속사 연구 | FACTORYTECHLAB: refused: unresolvable_upgrade |
| 75 | `research_ravencorvidreactor` | research | STARPORTTECHLAB -> RESEARCH_RAVENCORVIDREACTOR | none | - | 레이븐 에너지 연구 | STARPORTTECHLAB: refused: insufficient_researchers |
| 76 | `research_ravenrecalibratedexplosives` | research | STARPORTTECHLAB -> RESEARCH_RAVENRECALIBRATEDEXPLOSIVES | none | - | 재조정 폭약 | STARPORTTECHLAB: refused: insufficient_researchers |
| 77 | `research_smartservos` | research | FACTORYTECHLAB -> RESEARCH_SMARTSERVOS | none | level>1: 무기고 + previous level | 스마트 서보 연구 | FACTORYTECHLAB: sent |
| 78 | `research_stimpack` | research | BARRACKSTECHLAB -> BARRACKSTECHLABRESEARCH_STIMPACK | none | - | 스팀팩 연구 | BARRACKSTECHLAB: sent |
| 79 | `research_terraninfantryarmor` | research | ENGINEERINGBAY -> ENGINEERINGBAYRESEARCH_TERRANINFANTRYARMORLEVEL1 | none | level>1: 무기고 + previous level | 보병 방어력 업그레이드 | ENGINEERINGBAY: refused: unresolvable_upgrade |
| 80 | `research_terraninfantryweapons` | research | ENGINEERINGBAY -> ENGINEERINGBAYRESEARCH_TERRANINFANTRYWEAPONSLEVEL1 | none | level>1: 무기고 + previous level | 보병 공격력 업그레이드 | ENGINEERINGBAY: refused: unresolvable_upgrade |
| 81 | `research_terranshipweapons` | research | ARMORY -> ARMORYRESEARCH_TERRANSHIPWEAPONSLEVEL1 | none | level>1:  + previous level | 함선 공격력 업그레이드 | ARMORY: refused: unresolvable_upgrade |
| 82 | `research_terranstructurearmorupgrade` | research | ENGINEERINGBAY -> RESEARCH_TERRANSTRUCTUREARMORUPGRADE | none | - | 건물 방어력 | ENGINEERINGBAY: refused: unresolvable_upgrade |
| 83 | `research_terranvehicleandshipplating` | research | ARMORY -> ARMORYRESEARCH_TERRANVEHICLEANDSHIPPLATINGLEVEL1 | none | level>1:  + previous level | 차량 방어력 업그레이드 | ARMORY: refused: unresolvable_upgrade |
| 84 | `research_terranvehicleweapons` | research | ARMORY -> ARMORYRESEARCH_TERRANVEHICLEWEAPONSLEVEL1 | none | level>1:  + previous level | 차량 공격력 업그레이드 | ARMORY: refused: unresolvable_upgrade |
| 85 | `train_banshee` | train | STARPORT -> STARPORTTRAIN_BANSHEE | none | Tech Lab | 밴시 뽑아 | STARPORT: sent |
| 86 | `train_battlecruiser` | train | STARPORT -> STARPORTTRAIN_BATTLECRUISER | none | Tech Lab, 융합로 | 전투순양함 뽑아 | STARPORT: sent |
| 87 | `train_cyclone` | train | FACTORY -> TRAIN_CYCLONE | none | Tech Lab | 사이클론 뽑아 | FACTORY: sent |
| 88 | `train_ghost` | train | BARRACKS -> BARRACKSTRAIN_GHOST | none | Tech Lab, 유령 사관학교 | 유령 뽑아 | BARRACKS: sent |
| 89 | `train_hellbat` | train | FACTORY -> TRAIN_HELLBAT | none | 무기고 | 화염기갑병 뽑아 | FACTORY: refused: missing_producer_metadata |
| 90 | `train_hellion` | train | FACTORY -> FACTORYTRAIN_HELLION | none | - | 화염차 뽑아 | FACTORY: sent |
| 91 | `train_liberator` | train | STARPORT -> STARPORTTRAIN_LIBERATOR | none | - | 해방선 뽑아 | STARPORT: sent |
| 92 | `train_marauder` | train | BARRACKS -> BARRACKSTRAIN_MARAUDER | none | Tech Lab | 불곰 뽑아 | BARRACKS: sent |
| 93 | `train_marine` | train | BARRACKS -> BARRACKSTRAIN_MARINE | none | - | 해병 뽑아 | BARRACKS: sent |
| 94 | `train_medivac` | train | STARPORT -> STARPORTTRAIN_MEDIVAC | none | - | 의료선 뽑아 | STARPORT: sent |
| 95 | `train_raven` | train | STARPORT -> STARPORTTRAIN_RAVEN | none | Tech Lab | 밤까마귀 뽑아 | STARPORT: sent |
| 96 | `train_reaper` | train | BARRACKS -> BARRACKSTRAIN_REAPER | none | - | 사신 뽑아 | BARRACKS: sent |
| 97 | `train_scv` | train | COMMANDCENTER -> COMMANDCENTERTRAIN_SCV<br>ORBITALCOMMAND -> COMMANDCENTERTRAIN_SCV<br>PLANETARYFORTRESS -> COMMANDCENTERTRAIN_SCV | none | - | SCV 뽑아 | COMMANDCENTER: sent<br>ORBITALCOMMAND: sent<br>PLANETARYFORTRESS: sent |
| 98 | `train_siegetank` | train | FACTORY -> FACTORYTRAIN_SIEGETANK | none | Tech Lab | 탱크 뽑아 | FACTORY: sent |
| 99 | `train_thor` | train | FACTORY -> FACTORYTRAIN_THOR | none | Tech Lab, 무기고 | 토르 뽑아 | FACTORY: sent |
| 100 | `train_vikingfighter` | train | STARPORT -> STARPORTTRAIN_VIKINGFIGHTER | none | - | 바이킹 뽑아 | STARPORT: sent |
| 101 | `train_widowmine` | train | FACTORY -> FACTORYTRAIN_WIDOWMINE | none | - | 지뢰 뽑아 | FACTORY: sent |
| 102 | `unloadall` | other | BUNKER -> UNLOADALL_BUNKER<br>COMMANDCENTER -> UNLOADALL_COMMANDCENTER<br>COMMANDCENTERFLYING -> UNLOADALL | none | - | 전부 내려 | BUNKER: refused: unresolvable_ability<br>COMMANDCENTER: refused: unresolvable_ability<br>COMMANDCENTERFLYING: refused: unresolvable_ability |
| 103 | `unloadallat` | other | MEDIVAC -> UNLOADALLAT_MEDIVAC | point_or_unit (하차 위치) | - | 의료선 내려 | MEDIVAC: refused: no_ready_caster: no_cargo_to_unload |

Offline status summary: **88 of 127 (command, issuer) pairs sent**; 70 of 103 commands sent for at least one issuer.

<!-- END GENERATED TABLE -->
