"""Race-independent SC2 command catalog (Terran filled in; Protoss/Zerg arrive as data + aliases only).

One :class:`CommandEntry` per distinct non-generic command the s2client-api lists for a race (103 for Terran).  The
*facts* (issuer unit types, exact ability ids per issuer, upgrade levels, tech requirements) are GENERATED from the
s2client-api header extract and burnysc2 tables (``starcraft_commander/sc2_catalog_data.py``, produced by
``scripts/sc2_accuracy/gen_catalog_facts.py``); this module only adds the human layer (Korean/English aliases,
effect text, target noun, documented energy cost) and the state-aware evaluation.

Public API (see docs/sc2-command-catalog.md, "Public API"):

``get_command(id)``, ``all_commands()``, ``commands_by_category(category)``, ``commands_for_issuer(unit_type)``,
``find_by_alias(text)``, ``search(text, limit=5)``, ``evaluate(id, state, ...)``, ``issuable_now(state)``,
``direct_task_payload(id, ...)`` and ``catalog_tool_enums()``.

Nothing here talks to the game.  :class:`CatalogState` is a plain snapshot; ``PythonSC2BotAdapter.catalog_state()``
builds one from the live observation.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from starcraft_commander.sc2_catalog_data import TERRAN_COMMAND_FACTS

CATEGORIES: Final[tuple[str, ...]] = ("train", "build", "research", "morph", "effect", "behavior", "other")
RACE: Final[str] = "TERRAN"

TARGET_KIND_BY_HEADER: Final[dict[str, str]] = {
    "None": "none",
    "Point": "point",
    "Unit": "unit",
    "Unit, Point": "point_or_unit",
    "Point, Unit": "point_or_unit",
}

# --------------------------------------------------------------------------- human layer (annotations)
# token  : the semantic name the Direct path uses (execute_ability token, train/build subject, research family).
# mode   : how the adapter picks the SC2 target ("none", "enemy_unit", "enemy_point", "caster_point",
#          "explicit_point", "own_cargo", "own_damaged_bio", "mineral_patch", "supply_depot", "geyser").
# noun   : Korean name of what the target is (empty = no target).
# energy : documented energy cost (public game rule; the live checklist re-verifies it through Unit.energy).


@dataclass(frozen=True)
class _Ann:
    ko: tuple[str, ...]
    en: tuple[str, ...]
    desc_ko: str
    effect: str
    token: str | Mapping[str, str] = ""
    mode: str = "none"
    noun: str = ""
    energy: float | None = None


def _a(ko: str, en: str, desc_ko: str, effect: str, token: str | Mapping[str, str] = "", mode: str = "none",
       noun: str = "", energy: float | None = None) -> _Ann:
    return _Ann(tuple(x.strip() for x in ko.split("|") if x.strip()), tuple(x.strip() for x in en.split("|") if x.strip()),
                desc_ko, effect, token, mode, noun, energy)


_ANNOTATIONS: Final[dict[str, _Ann]] = {
    # ---- behavior
    "behavior_cloakon": _a("클로킹|은폐 켜|은폐 시작|투명화", "cloak on|cloak|turn cloak on", "유령/밴시를 은폐시킵니다 (에너지 소모)",
                           "Cloak the Ghost or Banshee.", {"GHOST": "ghost_cloak", "BANSHEE": "banshee_cloak"}, energy=25.0),
    "behavior_cloakoff": _a("클로킹 풀어|은폐 해제|은폐 꺼|투명화 해제", "cloak off|decloak", "유령/밴시의 은폐를 해제합니다",
                            "Turn the cloak off.", {"GHOST": "ghost_decloak", "BANSHEE": "banshee_decloak"}),
    "behavior_holdfireon": _a("사격 중지|홀드 파이어|발사 금지", "hold fire|hold fire on", "유령이 스스로 공격하지 않게 합니다 (은폐 유지용)",
                              "Ghost stops auto-attacking.", "ghost_hold_fire"),
    "behavior_holdfireoff": _a("사격 재개|홀드 파이어 해제|발사 허용", "hold fire off|release fire", "유령이 다시 자동 공격합니다",
                               "Ghost resumes auto-attacking.", "ghost_release_fire"),
    # ---- build (SCV structures)
    "build_armory": _a("무기고 지어|무기고", "build armory|armory", "무기고를 짓습니다 (차량/함선 업그레이드, 토르)", "Armory.", "ARMORY"),
    "build_barracks": _a("병영 지어|배럭 지어|병영|배럭", "build barracks|rax", "병영을 짓습니다 (해병/불곰/사신/유령)", "Barracks.", "BARRACKS"),
    "build_bunker": _a("벙커 지어|벙커", "build bunker|bunker", "벙커를 짓습니다 (보병 4기 수용)", "Bunker.", "BUNKER"),
    "build_commandcenter": _a("사령부 지어|확장|멀티|커맨드 지어", "build command center|expand|cc", "사령부를 짓습니다 (확장)", "Command Center.", "COMMANDCENTER"),
    "build_engineeringbay": _a("공학 연구소 지어|공학연구소|이베이", "build engineering bay|ebay", "공학 연구소를 짓습니다 (보병 업그레이드, 터렛)", "Engineering Bay.", "ENGINEERINGBAY"),
    "build_factory": _a("군수공장 지어|팩토리 지어|군수공장", "build factory|factory", "군수공장을 짓습니다 (탱크/사이클론/토르)", "Factory.", "FACTORY"),
    "build_fusioncore": _a("융합로 지어|융합로|퓨전 코어", "build fusion core|fusion core", "융합로를 짓습니다 (전투순양함)", "Fusion Core.", "FUSIONCORE"),
    "build_ghostacademy": _a("유령 사관학교 지어|고스트 아카데미|사관학교", "build ghost academy|ghost academy", "유령 사관학교를 짓습니다 (유령, 핵)", "Ghost Academy.", "GHOSTACADEMY"),
    "build_missileturret": _a("미사일 터렛 지어|터렛 지어|대공 포탑|미사일 포탑", "build missile turret|turret", "미사일 터렛을 짓습니다 (대공 방어/탐지)", "Missile Turret.", "MISSILETURRET"),
    "build_nuke": _a("핵 장전|핵 준비|핵 만들어|핵 생산", "arm nuke|build nuke", "유령 사관학교에서 핵을 장전합니다 (핵 발사 전 필요)", "Arm a nuke in the Ghost Academy silo.", "arm_nuke"),
    "build_reactor": _a("리액터 달아|리액터|쌍둥이 생산|동시 생산 애드온", "build reactor|reactor|add reactor", "병영/군수공장/우주공항에 리액터를 붙입니다 (동시 생산 2기)", "Reactor add-on.", "REACTOR"),
    "build_refinery": _a("가스 지어|정제소 지어|가스 건물|가스 채취 건물", "build refinery|refinery|gas", "간헐천 위에 정제소를 짓습니다", "Refinery on a geyser.", "REFINERY", "geyser", "가스 간헐천"),
    "build_sensortower": _a("감시탑 지어|센서 타워|감지탑", "build sensor tower|sensor tower", "감시탑을 짓습니다 (넓은 범위 적 감지)", "Sensor Tower.", "SENSORTOWER"),
    "build_starport": _a("우주공항 지어|스타포트 지어|우주공항", "build starport|starport", "우주공항을 짓습니다 (공중 유닛)", "Starport.", "STARPORT"),
    "build_supplydepot": _a("보급고 지어|서플 지어|보급고|서플라이", "build supply depot|depot|supply", "보급고를 짓습니다 (인구수 +8)", "Supply Depot.", "SUPPLYDEPOT"),
    "build_techlab": _a("기술실 달아|테크랩 달아|기술실|테크랩", "build tech lab|tech lab|techlab", "병영/군수공장/우주공항에 기술실을 붙입니다 (상위 유닛/연구)", "Tech Lab add-on.", "TECHLAB"),
    # ---- other
    "burrowdown": _a("지뢰 매복|지뢰 박아|매복", "burrow widow mine|burrow", "땅거미 지뢰를 매복시킵니다", "Burrow the Widow Mine.", "widow_mine_burrow"),
    "burrowup": _a("지뢰 매복 풀어|매복 해제", "unburrow widow mine|unburrow", "땅거미 지뢰의 매복을 풉니다", "Unburrow the Widow Mine.", "widow_mine_unburrow"),
    "land": _a("착륙|내려앉아|건물 착륙", "land|land building", "날아다니는 건물을 지정한 위치에 착륙시킵니다", "Land a flying building.", "land", "explicit_point", "착륙 위치"),
    "lift": _a("이륙|띄워|건물 띄워", "lift off|lift", "건물을 이륙시킵니다", "Lift a building off.", "lift_off"),
    "load": _a("태워|탑승|벙커에 넣어|의료선에 태워", "load|load unit|enter bunker", "대상 아군 유닛을 의료선/벙커에 태웁니다", "Load a unit.",
               {"MEDIVAC": "medivac_load", "BUNKER": "bunker_load"}, "own_cargo", "태울 아군 유닛"),
    "loadall": _a("사령부에 일꾼 넣어|일꾼 대피|SCV 대피", "load all|evacuate workers", "사령부/요새가 주변 SCV를 모두 태웁니다", "Command Center loads nearby SCVs.", "command_center_load_all"),
    "unloadall": _a("전부 내려|전부 내리기|벙커 비워|일꾼 내려", "unload all|empty bunker", "수송 중인 유닛을 모두 내립니다", "Unload everything.",
                    {"BUNKER": "bunker_unload_all", "COMMANDCENTER": "command_center_unload_all", "COMMANDCENTERFLYING": "command_center_unload_all"}),
    "unloadallat": _a("의료선 내려|여기서 내려|지정 위치에 하차", "unload at point|drop here", "의료선이 지정한 위치에서 모두 내립니다", "Medivac unloads at a point.", "medivac_unload_all", "caster_point", "하차 위치"),
    # ---- effect
    "effect_autoturret": _a("자동 터렛|오토터렛|터렛 설치", "auto turret|autoturret", "밤까마귀가 자동 터렛을 설치합니다", "Raven places an Auto-Turret.", "auto_turret", "caster_point", "설치 위치", 50.0),
    "effect_calldownmule": _a("뮬|뮤마|자원 채취 뮬|뮬 내려", "mule|call down mule", "궤도 사령부가 뮬을 내려 미네랄을 캡니다", "Call down a MULE.", "calldown_mule", "mineral_patch", "미네랄 광맥", 50.0),
    "effect_emp": _a("EMP|이엠피|전자기 충격", "emp|emp round", "유령이 지정 지점에 EMP를 쏩니다 (에너지/보호막 제거)", "Ghost EMP.", "emp", "enemy_point", "적 위치", 75.0),
    "effect_ghostsnipe": _a("저격|스나이프|유령 저격", "snipe|steady targeting", "유령이 적 생체 유닛을 저격합니다", "Ghost snipe.", "snipe", "enemy_unit", "적 유닛", 50.0),
    "effect_heal": _a("치료|힐|의료선 치료", "heal|medivac heal", "의료선이 부상당한 아군 생체 유닛을 치료합니다", "Medivac heal.", "medivac_heal", "own_damaged_bio", "부상 아군 생체 유닛"),
    "effect_hunterseekermissile": _a("추적 미사일|헌터 시커|사냥꾼 미사일", "hunter-seeker missile|seeker missile", "밤까마귀가 적 하나를 추적하는 미사일을 쏩니다", "Raven Hunter-Seeker Missile.", "hunter_seeker_missile", "enemy_unit", "적 유닛"),
    "effect_kd8charge": _a("KD8|케이디에이트|폭탄 던져|수류탄", "kd8 charge|grenade", "사신이 KD8 폭탄을 던집니다", "Reaper KD8 Charge.", "kd8_charge", "enemy_unit", "적 유닛/위치"),
    "effect_lockon": _a("락온|록온|사이클론 락온", "lock on|lockon", "사이클론이 적을 고정 사격합니다", "Cyclone Lock On.", "lock_on", "enemy_unit", "적 유닛"),
    "effect_medivacigniteafterburners": _a("부스터|가속|의료선 가속", "afterburners|medivac boost|boost", "의료선이 잠시 가속합니다", "Medivac speed boost.", "medivac_afterburners"),
    "effect_nukecalldown": _a("핵 발사|핵 쏴|전술핵", "nuke|tactical nuke|launch nuke", "유령이 지정 지점에 핵을 투하합니다 (장전된 핵 필요)", "Ghost calls down a nuclear strike.", "tactical_nuke", "enemy_point", "핵 투하 위치"),
    "effect_pointdefensedrone": _a("방어 드론|포인트 디펜스|요격 드론", "point defense drone|pdd", "밤까마귀가 적 투사체를 요격하는 방어 드론을 설치합니다", "Raven Point Defense Drone.", "point_defense_drone", "caster_point", "설치 위치"),
    "effect_repair": _a("수리|고쳐|SCV 수리", "repair|fix", "SCV/뮬이 손상된 기계/건물을 수리합니다", "Repair a mechanical unit or structure.", "repair", "repair", "손상된 아군 대상"),
    "effect_salvage": _a("벙커 해체|벙커 철거|벙커 환급", "salvage bunker|salvage", "벙커를 해체하고 자원을 돌려받습니다", "Salvage the Bunker.", "bunker_salvage"),
    "effect_scan": _a("스캔|스캐너|스캐너 스윕", "scan|scanner sweep", "궤도 사령부가 지정 지역을 스캔합니다", "Orbital Command scan.", "scanner_sweep", "explicit_point", "스캔 위치", 50.0),
    "effect_spray": _a("스프레이|낙서", "spray", "SCV가 스프레이를 뿌립니다 (장식)", "Spray decal.", "spray", "caster_point", "스프레이 위치"),
    "effect_stim": _a("스팀팩|스팀|전투 자극제", "stimpack|stim", "해병/불곰/벙커 속 보병이 체력을 소모해 이동/공격 속도를 올립니다", "Stimpack.",
                      {"MARINE": "stimpack", "MARAUDER": "stimpack", "BUNKER": "bunker_stimpack"}),
    "effect_supplydrop": _a("보급 드롭|서플 드롭|인구수 드롭", "supply drop|calldown extra supplies", "궤도 사령부가 보급고에 보급을 떨어뜨립니다 (보급고 +8)", "Extra supplies on a Supply Depot.", "supply_drop", "supply_depot", "보급고", 50.0),
    "effect_tacticaljump": _a("전술 도약|워프 점프|BC 점프", "tactical jump|warp jump", "전투순양함이 지정한 위치로 도약합니다", "Battlecruiser Tactical Jump.", "tactical_jump", "explicit_point", "도약 위치"),
    "effect_widowmineattack": _a("지뢰 발사|지뢰 공격|지뢰 쏴", "widow mine attack|fire mine", "매복한 땅거미 지뢰가 대상에게 미사일을 발사합니다", "Widow Mine Sentinel Missiles.", "widow_mine_attack", "enemy_unit", "적 유닛"),
    "effect_yamatogun": _a("야마토|야마토포", "yamato|yamato cannon", "전투순양함이 적 하나에 야마토 포를 쏩니다", "Battlecruiser Yamato Cannon.", "yamato", "enemy_unit", "적 유닛"),
    # ---- morph
    "morph_hellbat": _a("화염기갑병으로|헬벳 변신|헬벳 모드", "hellbat mode|morph to hellbat", "화염차를 화염기갑병으로 변신시킵니다 (무기고 필요)", "Hellion -> Hellbat.", "hellbat_mode"),
    "morph_hellion": _a("화염차로|헬리온 변신|헬리온 모드", "hellion mode|morph to hellion", "화염기갑병을 화염차로 되돌립니다", "Hellbat -> Hellion.", "hellion_mode"),
    "morph_liberatoraamode": _a("해방선 전투기 모드|해방선 대공 모드|해방선 공중전", "liberator fighter mode|liberator aa", "해방선을 대공(전투기) 모드로 되돌립니다", "Liberator AG -> AA.", "liberator_fighter_mode"),
    "morph_liberatoragmode": _a("해방선 방어 모드|해방선 지상 모드|해방선 지역 방어", "liberator defender mode|liberator ag", "해방선이 지정 위치에 지상 방어 구역을 만듭니다", "Liberator AA -> AG at a point.", "liberator_defender_mode", "enemy_point", "방어 구역 위치"),
    "morph_orbitalcommand": _a("궤도 사령부|오비탈|사령부 업그레이드", "orbital command|orbital|upgrade to orbital", "사령부를 궤도 사령부로 올립니다 (병영 필요; 스캔/뮬/보급 드롭)", "Command Center -> Orbital Command.", "ORBITALCOMMAND"),
    "morph_planetaryfortress": _a("행성 요새|플랭|행성요새", "planetary fortress|pf", "사령부를 행성 요새로 올립니다 (공학 연구소 필요; 방어)", "Command Center -> Planetary Fortress.", "PLANETARYFORTRESS"),
    "morph_siegemode": _a("시즈 모드|시즈|공성 모드", "siege mode|siege", "공성 전차를 시즈 모드로 전환합니다", "Siege Tank -> Sieged.", "siege_mode"),
    "morph_supplydepot_lower": _a("보급고 내려|서플 내려|보급고 낮춰", "lower supply depot|lower depot", "보급고를 내려 유닛이 지나가게 합니다", "Lower the Supply Depot.", "supply_depot_lower"),
    "morph_supplydepot_raise": _a("보급고 올려|서플 올려|보급고 막아", "raise supply depot|raise depot", "보급고를 올려 길을 막습니다", "Raise the Supply Depot.", "supply_depot_raise"),
    "morph_thorexplosivemode": _a("토르 폭발 모드|토르 일반 모드|토르 대공 폭발", "thor explosive mode|thor normal mode", "토르를 폭발 페이로드(범위 대공) 모드로 바꿉니다", "Thor -> Explosive Payload.", "thor_explosive_mode"),
    "morph_thorhighimpactmode": _a("토르 고충격 모드|토르 단일 대공|고충격 페이로드", "thor high impact mode|high impact payload", "토르를 고충격(단일 대상 대공) 모드로 바꿉니다", "Thor -> High Impact.", "thor_high_impact_mode"),
    "morph_unsiege": _a("시즈 풀어|시즈 해제|이동 모드", "unsiege|tank mode", "공성 전차의 시즈 모드를 풉니다", "Siege Tank -> Tank.", "unsiege"),
    "morph_vikingassaultmode": _a("바이킹 착륙|바이킹 지상 모드|바이킹 내려", "viking assault mode|land viking", "바이킹을 지상 돌격 모드로 착륙시킵니다", "Viking -> Assault.", "viking_assault_mode"),
    "morph_vikingfightermode": _a("바이킹 전투기 모드|바이킹 이륙|바이킹 날아", "viking fighter mode|viking air mode", "바이킹을 공중 전투기 모드로 되돌립니다", "Viking -> Fighter.", "viking_fighter_mode"),
    # ---- research
    "research_advancedballistics": _a("고급 탄도학|해방선 사거리|해방선 사거리 연구|전투순양함 사거리", "advanced ballistics|liberator range", "해방선 방어 구역 사거리를 늘립니다", "Liberator AG range.", "ADVANCEDBALLISTICS"),
    "research_bansheecloakingfield": _a("밴시 은신 연구|밴시 클로킹 연구|은폐장 연구|밴시 은폐 연구", "banshee cloaking field|cloak research|banshee cloak", "밴시가 은폐할 수 있게 연구합니다", "Cloaking Field.", "BANSHEECLOAKINGFIELD"),
    "research_bansheehyperflightrotors": _a("밴시 속도 연구|하이퍼플라이트 로터|밴시 이속", "hyperflight rotors|banshee speed", "밴시 이동 속도를 올립니다", "Banshee speed.", "BANSHEEHYPERFLIGHTROTORS"),
    "research_battlecruiserweaponrefit": _a("야마토 연구|전투순양함 무기 개조|야마토포 연구|전투순양함 특수화", "weapon refit|yamato research", "전투순양함 야마토/도약 사용을 연구합니다", "BC weapon refit (Yamato).", "BATTLECRUISERWEAPONREFIT"),
    "research_combatshield": _a("전투 방패 연구|컴뱃 쉴드|해병 체력 연구|전투방패", "combat shield|combat shield research", "해병 체력을 +10 올립니다", "Combat Shield.", "COMBATSHIELD"),
    "research_concussiveshells": _a("충격탄 연구|불곰 슬로우|컨커시브 쉘", "concussive shells", "불곰 공격이 대상을 느리게 합니다", "Concussive Shells.", "CONCUSSIVESHELLS"),
    "research_drillingclaws": _a("드릴 발톱 연구|지뢰 매복 연구|땅거미 지뢰 업글|드릴링 클로", "drilling claws", "땅거미 지뢰의 매복/재기동을 빠르게 합니다", "Drilling Claws.", "DRILLINGCLAWS"),
    "research_enhancedmunitions": _a("레이븐 강화 탄약|강화 탄약 연구", "enhanced munitions|raven munitions", "밤까마귀 미사일/기술을 강화합니다", "Raven Enhanced Munitions.", "ENHANCEDMUNITIONS"),
    "research_highcapacityfueltanks": _a("고용량 연료 탱크|연료 탱크 연구|바이킹 연료", "high capacity fuel tanks|fuel tanks", "우주공항 유닛의 연료/에너지 효율을 올립니다", "High Capacity Fuel Tanks.", "HIGHCAPACITYFUELTANKS"),
    "research_hisecautotracking": _a("고보안 자동 추적|터렛 사거리 연구|대공 포탑 사거리|하이섹", "hi-sec auto tracking|turret range", "터렛/건물 사정거리를 +1 올립니다", "Hi-Sec Auto Tracking.", "HISECAUTOTRACKING"),
    "research_infernalpreigniter": _a("화염 예열기 연구|화염차 업글|인퍼널 프리이그나이터|화염차 공격력 연구", "infernal pre-igniter|hellion damage", "화염차/화염기갑병의 공격력을 올립니다", "Infernal Pre-Igniter.", "INFERNALPREIGNITER"),
    "research_neosteelframe": _a("네오스틸 프레임|건물 사거리 연구|벙커 연구|벙커 사거리", "neosteel frame|bunker capacity", "벙커 수용량을 늘립니다", "Neosteel Frame.", "NEOSTEELFRAME"),
    "research_personalcloaking": _a("유령 은신 연구|개인 은폐 연구|유령 클로킹 연구|퍼스널 클로킹", "personal cloaking|ghost cloak research", "유령이 은폐할 수 있게 연구합니다", "Personal Cloaking.", "PERSONALCLOAKING"),
    "research_rapidfirelaunchers": _a("사이클론 속사 연구|속사 발사기|사이클론 연구", "rapid fire launchers|cyclone research", "사이클론 연사 속도를 올립니다", "Rapid Fire Launchers.", "RAPIDFIRELAUNCHERS"),
    "research_ravencorvidreactor": _a("레이븐 에너지 연구|코르비드 리액터|밤까마귀 에너지", "corvid reactor|raven energy", "밤까마귀 시작 에너지를 올립니다", "Corvid Reactor.", "RAVENCORVIDREACTOR"),
    "research_ravenrecalibratedexplosives": _a("재조정 폭약|밤까마귀 폭발 연구|레이븐 폭발", "recalibrated explosives", "밤까마귀 폭발 기술을 강화합니다", "Recalibrated Explosives.", "RAVENRECALIBRATEDEXPLOSIVES"),
    "research_smartservos": _a("스마트 서보 연구|토르 변신 연구|스마트 서보", "smart servos|transformation servos", "차량 변신 속도를 올립니다 (화염기갑병/바이킹)", "Smart Servos.", "SMARTSERVOS"),
    "research_stimpack": _a("스팀팩 연구|스팀팩 업그레이드|스팀 연구", "stimpack research|research stim", "해병/불곰 스팀팩을 연구합니다", "Stimpack.", "STIMPACK"),
    "research_terraninfantryarmor": _a("보병 방어력 업그레이드|보병 방어력 올려|보병 방업|보병 방어", "infantry armor|infantry armor upgrade", "보병 방어력을 한 단계 올립니다 (다음 단계는 상태로 결정)", "Terran Infantry Armor level 1-3.", "TERRANINFANTRYARMOR"),
    "research_terraninfantryweapons": _a("보병 공격력 업그레이드|보병 공격력 올려|보병 업그레이드|보병 공업|보병 업글", "infantry weapons|infantry weapons upgrade|infantry upgrade", "보병 공격력을 한 단계 올립니다 (다음 단계는 상태로 결정)", "Terran Infantry Weapons level 1-3.", "TERRANINFANTRYWEAPONS"),
    "research_terranshipweapons": _a("함선 공격력 업그레이드|공중 공격력 올려|함선 공업", "ship weapons|air weapons", "함선 공격력을 한 단계 올립니다", "Terran Ship Weapons level 1-3.", "TERRANSHIPWEAPONS"),
    "research_terranstructurearmorupgrade": _a("건물 방어력|건물 장갑 업그레이드|건물 방업", "structure armor|building armor", "건물 방어력을 올립니다", "Terran Building Armor.", "TERRANSTRUCTUREARMORUPGRADE"),
    "research_terranvehicleandshipplating": _a("차량 방어력 업그레이드|기계 방어력 올려|차량 함선 장갑|기계 방업", "vehicle and ship plating|vehicle armor", "차량/함선 방어력을 한 단계 올립니다", "Terran Vehicle and Ship Plating level 1-3.", "TERRANVEHICLEANDSHIPPLATING"),
    "research_terranvehicleweapons": _a("차량 공격력 업그레이드|탱크 공격력 올려|차량 공업|기계 공업", "vehicle weapons|vehicle upgrade", "차량 공격력을 한 단계 올립니다", "Terran Vehicle Weapons level 1-3.", "TERRANVEHICLEWEAPONS"),
    # ---- train
    "train_banshee": _a("밴시 뽑아|밴시 생산|밴시", "train banshee|banshee", "밴시를 생산합니다 (기술실)", "Banshee.", "BANSHEE"),
    "train_battlecruiser": _a("전투순양함 뽑아|배틀크루저 생산|BC 뽑아", "train battlecruiser|bc", "전투순양함을 생산합니다 (기술실+융합로)", "Battlecruiser.", "BATTLECRUISER"),
    "train_cyclone": _a("사이클론 뽑아|사이클론 생산", "train cyclone|cyclone", "사이클론을 생산합니다 (기술실)", "Cyclone.", "CYCLONE"),
    "train_ghost": _a("유령 뽑아|고스트 생산", "train ghost|ghost", "유령을 생산합니다 (기술실+유령 사관학교)", "Ghost.", "GHOST"),
    "train_hellbat": _a("화염기갑병 뽑아|헬벳 생산|헬벳 뽑아", "train hellbat|hellbat", "화염기갑병을 생산합니다 (무기고)", "Hellbat.", "HELLBAT"),
    "train_hellion": _a("화염차 뽑아|헬리온 생산", "train hellion|hellion", "화염차를 생산합니다", "Hellion.", "HELLION"),
    "train_liberator": _a("해방선 뽑아|해방선 생산", "train liberator|liberator", "해방선을 생산합니다", "Liberator.", "LIBERATOR"),
    "train_marauder": _a("불곰 뽑아|불곰 생산|마라우더", "train marauder|marauder", "불곰을 생산합니다 (기술실)", "Marauder.", "MARAUDER"),
    "train_marine": _a("해병 뽑아|마린 뽑아|마린 생산", "train marine|marine", "해병을 생산합니다", "Marine.", "MARINE"),
    "train_medivac": _a("의료선 뽑아|메디박 생산", "train medivac|medivac", "의료선을 생산합니다", "Medivac.", "MEDIVAC"),
    "train_raven": _a("밤까마귀 뽑아|레이븐 생산", "train raven|raven", "밤까마귀를 생산합니다 (기술실)", "Raven.", "RAVEN"),
    "train_reaper": _a("사신 뽑아|리퍼 생산", "train reaper|reaper", "사신을 생산합니다", "Reaper.", "REAPER"),
    "train_scv": _a("SCV 뽑아|일꾼 뽑아|일꾼 생산|건설로봇 생산", "train scv|scv|worker", "SCV를 생산합니다", "SCV.", "SCV"),
    "train_siegetank": _a("탱크 뽑아|공성 전차 뽑아|공성전차 생산", "train siege tank|tank", "공성 전차를 생산합니다 (기술실)", "Siege Tank.", "SIEGETANK"),
    "train_thor": _a("토르 뽑아|토르 생산", "train thor|thor", "토르를 생산합니다 (기술실+무기고)", "Thor.", "THOR"),
    "train_vikingfighter": _a("바이킹 뽑아|바이킹 생산", "train viking|viking", "바이킹을 생산합니다", "Viking.", "VIKINGFIGHTER"),
    "train_widowmine": _a("지뢰 뽑아|땅거미 지뢰 생산|위도우 마인", "train widow mine|widow mine", "땅거미 지뢰를 생산합니다", "Widow Mine.", "WIDOWMINE"),
}

# Korean display names of the structures/units the evaluator mentions.
TYPE_NAME_KO: Final[dict[str, str]] = {
    "SCV": "SCV", "MULE": "뮬", "COMMANDCENTER": "사령부", "COMMANDCENTERFLYING": "날아다니는 사령부", "ORBITALCOMMAND": "궤도 사령부",
    "ORBITALCOMMANDFLYING": "날아다니는 궤도 사령부", "PLANETARYFORTRESS": "행성 요새", "SUPPLYDEPOT": "보급고",
    "SUPPLYDEPOTLOWERED": "내려간 보급고", "BARRACKS": "병영", "BARRACKSFLYING": "날아다니는 병영", "BARRACKSTECHLAB": "병영 기술실",
    "FACTORY": "군수공장", "FACTORYFLYING": "날아다니는 군수공장", "FACTORYTECHLAB": "군수공장 기술실", "STARPORT": "우주공항",
    "STARPORTFLYING": "날아다니는 우주공항", "STARPORTTECHLAB": "우주공항 기술실", "ENGINEERINGBAY": "공학 연구소", "ARMORY": "무기고",
    "FUSIONCORE": "융합로", "GHOSTACADEMY": "유령 사관학교", "BUNKER": "벙커", "MISSILETURRET": "미사일 터렛", "SENSORTOWER": "감시탑",
    "REFINERY": "정제소", "MARINE": "해병", "MARAUDER": "불곰", "REAPER": "사신", "GHOST": "유령", "HELLION": "화염차",
    "HELLIONTANK": "화염기갑병", "SIEGETANK": "공성 전차", "SIEGETANKSIEGED": "시즈 모드 공성 전차", "CYCLONE": "사이클론",
    "THOR": "토르", "THORAP": "토르(폭발 모드)", "WIDOWMINE": "땅거미 지뢰", "WIDOWMINEBURROWED": "매복한 땅거미 지뢰",
    "MEDIVAC": "의료선", "VIKINGFIGHTER": "바이킹(전투기)", "VIKINGASSAULT": "바이킹(착륙)", "LIBERATOR": "해방선",
    "LIBERATORAG": "해방선(방어 모드)", "BANSHEE": "밴시", "RAVEN": "밤까마귀", "BATTLECRUISER": "전투순양함",
}

# Energy-cost documentation tokens that exist in the adapter table but belong to a differently named catalog token.
ENERGY_COST_BY_TOKEN: Final[dict[str, float]] = {
    ann_token: ann.energy
    for ann in _ANNOTATIONS.values()
    if ann.energy is not None
    for ann_token in ([ann.token] if isinstance(ann.token, str) else list(ann.token.values()))
}


@dataclass(frozen=True, eq=False)
class CommandEntry:
    """One catalog command.  Immutable, compared/hashed by identity; every fact traces to the generated data module."""

    id: str
    race: str
    category: str
    header_name: str
    header_id: int
    target_kind: str
    target_noun: str
    target_mode: str
    issuers: tuple[str, ...]
    abilities: Mapping[str, str]
    tokens: Mapping[str, str]
    aliases_ko: tuple[str, ...]
    aliases_en: tuple[str, ...]
    description_ko: str
    effect: str
    energy_cost: float | None
    produces: Mapping[str, tuple[str, bool, str]]
    upgrades: tuple[str, ...]
    upgrade_abilities: tuple[str, ...]
    upgrade_requirements: tuple[tuple[str, str], ...]
    unconfirmed_issuers: tuple[str, ...]

    @property
    def lowering(self) -> str:
        """Which Direct action type executes the entry."""

        if self.category == "train":
            return "train_unit"
        if self.category == "research":
            return "research_upgrade"
        if self.category == "build" and self.id != "build_nuke":
            return "build_structure"
        if self.id in {"morph_orbitalcommand", "morph_planetaryfortress"}:
            return "build_structure"
        if self.id == "effect_repair":
            return "repair"
        return "execute_ability"

    @property
    def valid_issuers(self) -> tuple[str, ...]:
        """Issuers the game really honours: a flying building cannot build an add-on."""

        if self.id in {"build_techlab", "build_reactor"}:
            return tuple(i for i in self.issuers if not i.endswith("FLYING"))
        return self.issuers

    @property
    def subject(self) -> str:
        """The Direct-path subject: unit/structure/upgrade-family name, or the ability token."""

        first = next(iter(self.tokens.values()), "")
        return first

    def token_for(self, issuer: str | None = None) -> str:
        if issuer and issuer in self.tokens:
            return self.tokens[issuer]
        return self.subject

    @property
    def tokens_distinct(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(self.tokens.values()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "race": self.race, "category": self.category, "issuers": list(self.issuers),
            "target_kind": self.target_kind, "target_noun": self.target_noun, "aliases_ko": list(self.aliases_ko),
            "aliases_en": list(self.aliases_en), "description_ko": self.description_ko, "effect": self.effect,
            "energy_cost": self.energy_cost, "upgrades": list(self.upgrades), "tokens": dict(self.tokens),
            "abilities": dict(self.abilities),
        }


def _build_entry(fact: Mapping[str, Any]) -> CommandEntry:
    ann = _ANNOTATIONS[fact["id"]]
    issuers = tuple(fact["issuers"])
    if isinstance(ann.token, str):
        tokens = {issuer: ann.token for issuer in issuers}
    else:
        tokens = {issuer: ann.token.get(issuer, next(iter(ann.token.values()))) for issuer in issuers}
    mode = ann.mode
    kind = TARGET_KIND_BY_HEADER[fact["header_target"]]
    return CommandEntry(
        id=fact["id"], race=RACE, category=fact["category"], header_name=fact["header_name"], header_id=fact["header_id"],
        target_kind=kind, target_noun=ann.noun, target_mode=mode, issuers=issuers, abilities=dict(fact["abilities"]),
        tokens=tokens, aliases_ko=ann.ko, aliases_en=ann.en, description_ko=ann.desc_ko, effect=ann.effect,
        energy_cost=ann.energy, produces=dict(fact["produces"]), upgrades=tuple(fact["upgrades"]),
        upgrade_abilities=tuple(fact["upgrade_abilities"]), upgrade_requirements=tuple(fact["upgrade_requirements"]),
        unconfirmed_issuers=tuple(fact["not_in_python_sc2_unit_abilities"]),
    )


def _build_catalog() -> dict[str, CommandEntry]:
    entries: dict[str, CommandEntry] = {}
    missing = [f["id"] for f in TERRAN_COMMAND_FACTS if f["id"] not in _ANNOTATIONS]
    if missing:  # import-time drift guard: a regenerated fact row without a human annotation must not load silently
        raise RuntimeError(f"sc2_catalog: facts without annotation: {missing}")
    for fact in TERRAN_COMMAND_FACTS:
        entries[fact["id"]] = _build_entry(fact)
    return entries


_CATALOG: Final[dict[str, CommandEntry]] = _build_catalog()


# --------------------------------------------------------------------------- lookup API


def all_commands(race: str = RACE) -> tuple[CommandEntry, ...]:
    return tuple(entry for entry in _CATALOG.values() if entry.race == race.upper())


def get_command(command_id: str) -> CommandEntry:
    """Entry by stable id (``research_terraninfantryweapons``); ``KeyError`` when unknown."""

    return _CATALOG[str(command_id).strip().lower()]


def commands_by_category(category: str) -> tuple[CommandEntry, ...]:
    if category not in CATEGORIES:
        raise ValueError(f"unknown category {category!r}")
    return tuple(entry for entry in all_commands() if entry.category == category)


def commands_for_issuer(unit_type: str) -> tuple[CommandEntry, ...]:
    """Every command the unit/structure type can issue (UnitTypeId name, with or without ``TERRAN_``)."""

    name = _norm_type(unit_type)
    return tuple(entry for entry in all_commands() if name in entry.issuers)


def _norm_type(value: object) -> str:
    return str(value or "").strip().upper().removeprefix("TERRAN_").replace("_", "")


_ALIAS_STRIP = re.compile(r"[\s\-_.,!?~]+")


def normalize_alias(text: str) -> str:
    return _ALIAS_STRIP.sub("", str(text or "").casefold())


def _alias_index() -> dict[str, tuple[CommandEntry, ...]]:
    index: dict[str, list[CommandEntry]] = {}
    for entry in all_commands():
        keys = {normalize_alias(a) for a in (*entry.aliases_ko, *entry.aliases_en, entry.id, entry.header_name)}
        keys.update(normalize_alias(t) for t in entry.tokens_distinct)
        for key in keys:
            if key:
                index.setdefault(key, []).append(entry)
    return {key: tuple(dict.fromkeys(value)) for key, value in index.items()}


_ALIASES: Final[dict[str, tuple[CommandEntry, ...]]] = _alias_index()


def find_by_alias(text: str, *, category: str | None = None) -> tuple[CommandEntry, ...]:
    """Entries whose alias/id/token equals ``text`` after normalisation (empty when none).

    More than one entry back means the phrase is ambiguous across categories (``스팀팩`` = the stim EFFECT and the stim
    RESEARCH); the interpreter picks by state (``evaluate``) or asks once.
    """

    found = _ALIASES.get(normalize_alias(text), ())
    if category:
        found = tuple(entry for entry in found if entry.category == category)
    return found


def search(text: str, limit: int = 5) -> tuple[CommandEntry, ...]:
    """Ranked fuzzy lookup for free text: exact alias first, then alias contained in the phrase (longest first)."""

    needle = normalize_alias(text)
    if not needle:
        return ()
    scored: dict[str, tuple[int, CommandEntry]] = {}
    for key, entries in _ALIASES.items():
        if key == needle:
            score = 10_000
        elif len(key) >= 2 and key in needle:
            score = len(key) * 10
        elif len(needle) >= 3 and needle in key:
            score = len(needle)
        else:
            continue
        for entry in entries:
            if entry.id not in scored or scored[entry.id][0] < score:
                scored[entry.id] = (score, entry)
    ranked = sorted(scored.values(), key=lambda item: (-item[0], item[1].id))
    return tuple(entry for _, entry in ranked[:limit])


def catalog_tool_enums() -> dict[str, tuple[str, ...]]:
    """Catalog-derived enums for the realtime tool schema / prompt."""

    return {
        "command_ids": tuple(sorted(_CATALOG)),
        "categories": CATEGORIES,
        "ability_tokens": tuple(sorted({t for e in all_commands() if e.lowering == "execute_ability" for t in e.tokens_distinct})),
        "research_families": tuple(sorted(e.subject for e in commands_by_category("research"))),
    }


def ability_tokens_for_entry(entry: CommandEntry) -> dict[str, tuple[str, tuple[str, ...]]]:
    """``token -> (exact ability name, caster unit types)`` for an execute_ability entry (adapter table source)."""

    out: dict[str, tuple[str, tuple[str, ...]]] = {}
    by_token: dict[str, list[str]] = {}
    for issuer in entry.valid_issuers:
        by_token.setdefault(entry.tokens[issuer], []).append(issuer)
    for token, issuers in by_token.items():
        out[token] = (entry.abilities[issuers[0]], tuple(issuers))
    return out


# --------------------------------------------------------------------------- state snapshot + evaluation


@dataclass(frozen=True)
class Owned:
    """One own unit/structure as the evaluator needs it."""

    type_name: str
    tag: int = 0
    ready: bool = True
    idle: bool = True
    energy: float | None = None
    addon: str = ""  # "techlab" | "reactor" | "none" | "" (unknown)
    is_structure: bool = False


@dataclass(frozen=True)
class CatalogState:
    """A plain snapshot of the bot state the evaluator reads (build one with ``PythonSC2BotAdapter.catalog_state``)."""

    owned: tuple[Owned, ...] = ()
    upgrades_done: frozenset[str] = frozenset()
    upgrades_pending: frozenset[str] = frozenset()
    enemies_visible: int | None = None
    free_geysers: int | None = None
    mineral_patches: int | None = None
    minerals: int | None = None
    vespene: int | None = None
    can_afford: Callable[[str, str], bool | None] | None = field(default=None, compare=False)
    """``can_afford(kind, name)``: kind in {"unit","structure","upgrade","ability"}; ``None`` = unknown (never a refusal)."""

    def of_type(self, *names: str) -> tuple[Owned, ...]:
        wanted = set(names)
        return tuple(o for o in self.owned if o.type_name in wanted)


@dataclass(frozen=True)
class CommandEvaluation:
    command_id: str
    ok: bool
    reason: str = ""
    """Machine reason in the round-4 refusal shape (``missing_prerequisite: ARMORY``) or ``""`` when issuable."""
    reason_ko: str = ""
    issuer_tags: tuple[int, ...] = ()
    issuer_type: str = ""
    upgrade: str = ""
    """For research: the UpgradeId member chosen from state (next level)."""
    ability: str = ""
    token: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id, "ok": self.ok, "reason": self.reason, "reason_ko": self.reason_ko,
            "issuer_tags": list(self.issuer_tags), "issuer_type": self.issuer_type, "upgrade": self.upgrade,
            "ability": self.ability, "token": self.token,
        }


REFUSAL_KO: Final[dict[str, str]] = {
    "no_matching_caster": "이 명령을 내릴 수 있는 유닛/건물이 없습니다",
    "no_ready_idle_researcher": "연구를 할 수 있는 준비된 유휴 건물이 없습니다",
    "no_ready_idle_producer": "준비된 유휴 생산 건물이 없습니다",
    "producer_needs_techlab": "이 명령은 기술실(Tech Lab)이 붙은 건물이 필요합니다",
    "already_researched": "이미 연구가 끝났습니다",
    "research_already_pending": "이미 연구 중입니다",
    "no_next_level": "더 올릴 단계가 없습니다",
    "not_enough_energy": "에너지가 부족합니다",
    "unaffordable": "자원이 부족합니다",
    "supply_blocked": "보급(인구수)이 부족합니다 — 보급고가 필요합니다",
    "insufficient_workers": "사용할 수 있는 SCV가 없습니다",
    "no_enemy_target": "대상으로 삼을 보이는 적이 없습니다",
    "no_free_geyser": "사용 가능한 가스 간헐천이 없습니다",
    "no_mineral_patch": "뮬을 내릴 미네랄 광맥이 없습니다",
    "no_supply_depot": "보급 드롭을 받을 완성된 보급고가 없습니다",
    "no_cargo_unit": "태울 수 있는 아군 유닛이 없습니다",
    "no_damaged_biological_unit": "치료할 부상 생체 유닛이 없습니다",
    "no_damaged_repair_target": "수리할 손상된 대상이 없습니다",
    "explicit_point_required": "어디에 쓸지 위치가 필요합니다",
    "unknown_command": "알 수 없는 명령입니다",
    "addon_already_attached": "이미 애드온이 붙어 있습니다",
    "structure_busy": "생산/연구 중인 건물에는 쓸 수 없습니다 — 대기열이 빌 때까지 기다리세요",
}


def refusal_korean(reason: str) -> str:
    """Korean sentence for a catalog refusal code (round-4 shape ``code`` or ``code: ARG``)."""

    head, _, tail = str(reason or "").partition(":")
    head, tail = head.strip(), tail.strip()
    if head in {"missing_prerequisite", "prerequisite_under_construction"} and tail:
        name = TYPE_NAME_KO.get(tail, tail)
        if head == "missing_prerequisite":
            return f"선행 건물이 없습니다 — {name}이(가) 먼저 필요합니다"
        return f"선행 건물 {name}이(가) 아직 건설 중입니다 — 완성된 뒤에 다시 말해 주세요"
    if head == "missing_prerequisite_upgrade" and tail:
        return f"먼저 {tail} 업그레이드가 필요합니다"
    if head == "wrong_issuer" and tail:
        return f"{TYPE_NAME_KO.get(tail, tail)}이(가) 있어야 이 명령을 쓸 수 있습니다"
    return REFUSAL_KO.get(head, "")


def _refuse(entry_id: str, reason: str, **extra: Any) -> CommandEvaluation:
    return CommandEvaluation(command_id=entry_id, ok=False, reason=reason, reason_ko=refusal_korean(reason), **extra)


def _equivalent_types(name: str) -> frozenset[str]:
    from starcraft_commander.terran_prerequisites import satisfying_types

    base = {
        "COMMANDCENTER": frozenset({"COMMANDCENTER", "ORBITALCOMMAND", "PLANETARYFORTRESS", "COMMANDCENTERFLYING", "ORBITALCOMMANDFLYING"}),
    }
    return base.get(name) or satisfying_types(name)


def _has_structure(state: CatalogState, name: str) -> tuple[bool, bool]:
    """``(exists, ready)`` for a required structure (flying/morphed forms count)."""

    owned = state.of_type(*_equivalent_types(name))
    return bool(owned), any(o.ready for o in owned)


def _prerequisite_reason(state: CatalogState, required: str) -> str:
    if not required:
        return ""
    exists, ready = _has_structure(state, required)
    if ready:
        return ""
    return f"prerequisite_under_construction: {required}" if exists else f"missing_prerequisite: {required}"


def upgrade_progress_name(entry: CommandEntry, state: CatalogState) -> tuple[str, str]:
    """``(upgrade, refusal)``: the next UpgradeId to research chosen from state, or a refusal reason.

    Multi-level families (infantry/vehicle/ship weapons and armor) climb 1 -> 2 -> 3; single upgrades are ready until
    done.  A level whose predecessor is still being researched is ``research_already_pending`` (one at a time per
    family; the game queues the second level only after the first completes).
    """

    levels = entry.upgrades
    if not levels:
        return "", ""
    if len(levels) == 1:
        name = levels[0]
        if name in state.upgrades_done:
            return "", "already_researched"
        if name in state.upgrades_pending:
            return "", "research_already_pending"
        return name, ""
    for name in levels:
        if name in state.upgrades_done:
            continue
        if name in state.upgrades_pending:
            return "", "research_already_pending"
        return name, ""
    return "", "no_next_level"


def evaluate(
    command_id: str,
    state: CatalogState,
    *,
    issuer: str | None = None,
    upgrade: str | None = None,
) -> CommandEvaluation:
    """Can ``command_id`` be issued right now?  Pure function of the snapshot (no game access).

    Returns the issuer tags/type, the chosen upgrade level (research), and the exact ability, or a refusal in the
    round-4 shape with a Korean sentence.  ``issuer`` pins an issuer unit type.
    """

    try:
        entry = get_command(command_id)
    except KeyError:
        return CommandEvaluation(command_id=str(command_id), ok=False, reason="unknown_command", reason_ko=REFUSAL_KO["unknown_command"])
    issuer_types = tuple(i for i in entry.valid_issuers if not issuer or i == _norm_type(issuer))
    candidates = [o for o in state.owned if o.type_name in issuer_types]
    category = entry.category
    structure_issuer = bool(issuer_types) and all(i in _STRUCTURE_ISSUER_TYPES for i in issuer_types)

    # --- scv-built structures
    if entry.lowering == "build_structure" and "SCV" in entry.issuers:
        workers = state.of_type("SCV")
        if not workers:
            return _refuse(entry.id, "insufficient_workers")
        _, required = _produces(entry, "SCV")
        reason = _prerequisite_reason(state, required) or _geyser_reason(entry, state) or _afford_reason(state, "structure", entry.subject)
        if reason:
            return _refuse(entry.id, reason)
        return _ok(entry, workers[:1], "SCV")

    if not candidates:
        wanted = issuer_types[0] if issuer_types else ""
        if structure_issuer:
            code = "no_ready_idle_researcher" if category == "research" else "no_ready_idle_producer" if category == "train" else "wrong_issuer: " + wanted
            return _refuse(entry.id, code)
        return _refuse(entry.id, "no_matching_caster")

    usable = [o for o in candidates if o.ready]
    if not usable:
        return _refuse(entry.id, f"prerequisite_under_construction: {candidates[0].type_name}")
    if structure_issuer or category in {"train", "research"} or entry.id in _IDLE_REQUIRED:
        idle = [o for o in usable if o.idle]
        if not idle:
            return _refuse(entry.id, "structure_busy")
        usable = idle

    if category == "train":
        unit, needs_lab, required = next((v for v in entry.produces.values()), ("", False, ""))
        if needs_lab:
            equipped = [o for o in usable if o.addon in {"techlab", ""}]
            if not equipped:
                return _refuse(entry.id, "producer_needs_techlab")
            usable = equipped
        reason = _prerequisite_reason(state, required) or _afford_reason(state, "unit", unit)
        if reason:
            return _refuse(entry.id, reason)
        return _ok(entry, usable, usable[0].type_name)

    if category == "research":
        chosen, refusal = (upgrade, "") if upgrade else upgrade_progress_name(entry, state)
        if refusal:
            return _refuse(entry.id, refusal)
        reason = _research_prerequisite_reason(entry, state, chosen)
        if reason:
            return _refuse(entry.id, reason)
        reason = _afford_reason(state, "upgrade", chosen or entry.subject)
        if reason:
            return _refuse(entry.id, reason)
        ability = entry.upgrade_abilities[entry.upgrades.index(chosen)] if chosen in entry.upgrades else next(iter(entry.abilities.values()))
        return _ok(entry, usable, usable[0].type_name, upgrade=chosen, ability=ability)

    if entry.id in {"build_techlab", "build_reactor"} or entry.id in {"morph_orbitalcommand", "morph_planetaryfortress"}:
        unit, _, required = next((v for v in entry.produces.values()), ("", False, ""))
        if entry.id in {"build_techlab", "build_reactor"}:
            bare = [o for o in usable if o.addon in {"none", ""}]
            if not bare:
                return _refuse(entry.id, "addon_already_attached")
            usable = bare
        reason = _prerequisite_reason(state, required) or _afford_reason(state, "structure", unit or entry.subject)
        if reason:
            return _refuse(entry.id, reason)
        return _ok(entry, usable, usable[0].type_name)

    if entry.energy_cost is not None:
        charged = [o for o in usable if o.energy is None or o.energy >= entry.energy_cost]
        if not charged:
            return _refuse(entry.id, "not_enough_energy")
        usable = charged
    mode = entry.target_mode
    if mode in {"enemy_unit", "enemy_point"} and state.enemies_visible == 0 and entry.id not in {"morph_liberatoragmode"}:
        return _refuse(entry.id, "no_enemy_target")
    if mode == "mineral_patch" and state.mineral_patches == 0:
        return _refuse(entry.id, "no_mineral_patch")
    if mode == "supply_depot" and not state.of_type("SUPPLYDEPOT", "SUPPLYDEPOTLOWERED"):
        return _refuse(entry.id, "no_supply_depot")
    if mode == "own_cargo" and not [o for o in state.owned if not o.is_structure and o.type_name not in {"SCV", "MEDIVAC", "MULE"}]:
        return _refuse(entry.id, "no_cargo_unit")
    return _ok(entry, usable, usable[0].type_name)


_STRUCTURE_ISSUER_TYPES: Final[frozenset[str]] = frozenset(
    {
        "COMMANDCENTER", "COMMANDCENTERFLYING", "ORBITALCOMMAND", "ORBITALCOMMANDFLYING", "PLANETARYFORTRESS", "BARRACKS",
        "BARRACKSFLYING", "FACTORY", "FACTORYFLYING", "STARPORT", "STARPORTFLYING", "SUPPLYDEPOT", "SUPPLYDEPOTLOWERED",
        "BUNKER", "GHOSTACADEMY", "ENGINEERINGBAY", "ARMORY", "FUSIONCORE", "BARRACKSTECHLAB", "FACTORYTECHLAB",
        "STARPORTTECHLAB", "MISSILETURRET", "SENSORTOWER", "REFINERY",
    }
)
_IDLE_REQUIRED: Final[frozenset[str]] = frozenset({"lift", "build_nuke"})


def _produces(entry: CommandEntry, issuer: str) -> tuple[str, str]:
    unit, _, required = entry.produces.get(issuer, ("", False, ""))
    return unit, required


def _geyser_reason(entry: CommandEntry, state: CatalogState) -> str:
    if entry.target_mode == "geyser" and state.free_geysers == 0:
        return "no_free_geyser"
    return ""


def _afford_reason(state: CatalogState, kind: str, name: str) -> str:
    if state.can_afford is None or not name:
        return ""
    verdict = state.can_afford(kind, name)
    return "" if verdict is not False else "unaffordable"


def _research_prerequisite_reason(entry: CommandEntry, state: CatalogState, upgrade: str) -> str:
    if upgrade not in entry.upgrades:
        return ""
    building, previous = entry.upgrade_requirements[entry.upgrades.index(upgrade)]
    reason = _prerequisite_reason(state, building)
    if reason:
        return reason
    if previous and previous not in state.upgrades_done:
        return f"missing_prerequisite_upgrade: {previous}"
    return ""


def _ok(entry: CommandEntry, owned: Iterable[Owned], issuer_type: str, *, upgrade: str = "", ability: str = "") -> CommandEvaluation:
    picked = tuple(o for o in owned if o.type_name == issuer_type)
    return CommandEvaluation(
        command_id=entry.id, ok=True, issuer_tags=tuple(o.tag for o in picked), issuer_type=issuer_type, upgrade=upgrade,
        ability=ability or entry.abilities.get(issuer_type, next(iter(entry.abilities.values()), "")), token=entry.token_for(issuer_type),
    )


def issuable_now(state: CatalogState, *, categories: Iterable[str] | None = None) -> tuple[CommandEvaluation, ...]:
    """Every command that would be accepted right now (the interpreter's candidate set)."""

    wanted = set(categories) if categories else None
    out = []
    for entry in all_commands():
        if wanted is not None and entry.category not in wanted:
            continue
        result = evaluate(entry.id, state)
        if result.ok:
            out.append(result)
    return tuple(out)


# --------------------------------------------------------------------------- Direct-path request builder


def direct_task_payload(
    command_id: str,
    *,
    issuer: str | None = None,
    location: str = "",
    level: int | None = None,
) -> dict[str, Any]:
    """The provider-output (policy modulation) payload that lowers ``command_id`` on the Direct path.

    It goes through the REAL ``compile_policy_modulation_provider_output`` -> ``route_policy_vector`` ->
    ``SC2RuntimeExecutor`` chain; nothing here builds a game request by hand.  ``level`` pins a research level
    (1-3) instead of choosing the next one from state.
    """

    entry = get_command(command_id)
    issuer_type = _norm_type(issuer) if issuer else (entry.valid_issuers[0] if entry.valid_issuers else "")
    lowering = entry.lowering
    if lowering == "execute_ability":
        task: dict[str, Any] = {
            "task_type": "execute_ability", "ability": entry.token_for(issuer_type),
            "unit_classes": [f"TERRAN_{issuer_type}"],
        }
        if entry.id == "effect_nukecalldown":
            task["production_targets"] = ["TERRAN_NUKE"]
    elif lowering == "train_unit":
        task = {"task_type": "train_unit", "production_targets": [f"TERRAN_{entry.subject}"]}
    elif lowering == "research_upgrade":
        target = entry.subject
        if level and len(entry.upgrades) > 1:
            target = entry.upgrades[level - 1]
        task = {"task_type": "research_upgrade", "production_targets": [target]}
    elif lowering == "repair":
        task = {"task_type": "repair", "production_targets": ["TERRAN_BUNKER"]}
    else:
        subject = entry.subject
        token = subject if subject in {"TECHLAB", "REACTOR"} else subject
        if subject in {"TECHLAB", "REACTOR"}:
            token = f"{issuer_type.removesuffix('FLYING')}_{subject}"
        elif subject in {"ORBITALCOMMAND", "PLANETARYFORTRESS"}:
            token = f"TERRAN_{subject}"
        else:
            token = f"TERRAN_{subject}"
        task = {"task_type": "build_structure", "production_targets": [token]}
        if entry.target_mode == "geyser":
            location = location or "self_geyser"
        else:
            location = location or "home"
    if location:
        task["location_intent"] = location
    return {"goal": f"catalog {entry.id}", "command_layer": "operation" if lowering != "execute_ability" else "micro", "tactical_task": task}
