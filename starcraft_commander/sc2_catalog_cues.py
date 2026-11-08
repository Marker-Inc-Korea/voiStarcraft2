"""Plain-language intent cues for the Terran command catalog (data only; no logic).

A cue is ``"part+part"``: the phrase (lower-cased, spaces/punctuation stripped) matches when EVERY ``part`` has at least
one of its ``a|b|c`` alternatives as a substring.  A cue that starts with ``-`` (``"-연구|업그레이드"``) is an ENTRY-level
veto: when the phrase contains any alternative the command is not offered at all (``밴시 은신 연구`` must not mean the
Banshee cloak BEHAVIOR).  The user speaks intent
("탱크 사거리 늘려줘", "밴시 투명해지게 연구해줘", "인구수 막혔어"), not ability names; these cues are how the catalog
maps that intent to a command.  They are matched by :func:`starcraft_commander.sc2_catalog.search`, never by the game.
Several commands matching one phrase is deliberate: the interpreter resolves it from state or asks once.
"""

from __future__ import annotations

from typing import Final

# Verb groups reused below (kept as text so a cue reads like the sentence it matches).
_RESEARCH = "연구|개발|업그레이드|업글|가능하게|있게|research|upgrade|unlock"
_NOT_RESEARCH = "-연구|개발|업그레이드|업글|research|upgrade"

CUES: Final[dict[str, tuple[str, ...]]] = {
    # ------------------------------------------------------------------ research (24)
    "research_terraninfantryweapons": (
        "보병+공격|공업|데미지|화력|무기|무장", "공업", "해병+공격력+올|업|강화", "infantry+weapon|attack|damage", "bio+damage|attack|weapon",
        "보병+업", "infantry+upgrade", "infantry+upgrade+weapon|attack|damage", "바이오+공격|데미지", "-방어력|방어구|방업|아머|armor",
    ),
    "research_terraninfantryarmor": (
        "보병+방어|방업|아머|맷집|단단|방어구", "방업", "해병+방어력+올|업|강화", "infantry+armor", "bio+armor", "보병+업", "infantry+upgrade", "infantry+upgrade+armor",
        "바이오+방어|아머", "-무기|공격력|공업|데미지|weapon|attack|damage",
    ),
    "research_terranstructurearmorupgrade": (
        "건물+방어|튼튼|단단|장갑|방업|체력", "building+armor|tough|sturdy|hp", "structure+armor", "건물+업그레이드|업글",
    ),
    "research_hisecautotracking": (
        "터렛+사거리|사정거리|멀리|업그레이드|업글|연구", "포탑+사거리|사정거리|멀리", "대공+사거리", "turret+range|upgrade", "하이섹|고보안",
        "건물+사거리+늘",
    ),
    "research_terranvehicleweapons": (
        "차량+공격|공업|데미지|화력|무기|무장", "탱크+공격력+올|업|강화", "기계+공격|공업", "메카닉+공업|공격", "vehicle+weapon|attack|damage|upgrade",
        "mech+attack|damage|weapon", "토르+공격력+올|업", "사이클론+공격력+올|업",
    ),
    "research_terranvehicleandshipplating": (
        "차량+방어|방업|장갑|아머|방어구", "탱크+방어력+올|업|강화", "기계+방어|방업", "메카닉+방업|방어", "vehicle+armor|plating", "mech+armor",
        "함선+방어|방업|장갑|방어구", "공중+방어|방업|장갑",
    ),
    "research_terranshipweapons": (
        "공중+공격력|공업|데미지|화력", "함선+공격|공업|데미지|무기|무장", "항공+공격|공업", "하늘+공격력|공업", "ship+weapon|attack|damage",
        "air+weapon|attack|damage|upgrade", "밴시+공격력+올|업", "바이킹+공격력+올|업", "배틀크루저+공격력+올|업",
    ),
    "research_bansheecloakingfield": (
        "밴시+은신|은시|투명|클로킹|은폐|안보+연구|개발|업그레이드|업글|가능|있게|research|unlock",
        "banshee+cloak|invisible|stealth|hidden+research|upgrade|unlock|make",
        "밴시+투명해지|은신가능|클로킹가능|은폐가능", "은폐장", "cloaking field",
    ),
    "research_bansheehyperflightrotors": (
        "밴시+빠르|빨라|빨리|속도|이속|스피드|가속+연구|개발|업그레이드|업글|있게|해줘|research|upgrade|faster",
        "banshee+speed|fast|faster+research|upgrade", "하이퍼플라이트", "로터",
    ),
    "research_battlecruiserweaponrefit": (
        "야마토+연구|개발|가능|있게|업그레이드|research|unlock", "전투순양함+무기|개조|특수|야마토+연구|개발|업그레이드|가능",
        "배틀크루저+야마토+연구|개발|가능|있게", "bc+yamato+research|unlock", "yamato+research|unlock", "weapon refit|무기개조",
    ),
    "research_combatshield": (
        "해병+방패|쉴드|체력+연구|개발|업그레이드|업글|올려|늘려", "해병+hp|피+늘|올|연구", "컴뱃쉴드|전투방패", "marine+shield|health|hp",
        "combat+shield",
    ),
    "research_concussiveshells": (
        "불곰+슬로우|둔화|느리|감속|속박+연구|개발|업그레이드|업글|해줘", "충격탄|컨커시브", "marauder+slow|concussive",
        "불곰+공격+느려지",
    ),
    "research_drillingclaws": (
        "지뢰+매복+연구|개발|업그레이드|업글|빠르|빨라|드릴", "드릴+발톱|클로|연구", "widow+upgrade|burrow+faster|drilling",
        "땅거미+연구|업그레이드|업글",
    ),
    "research_infernalpreigniter": (
        "화염차+업그레이드|업글|연구|공격력|데미지|강화+올|해줘|연구|개발|업", "헬리온+업그레이드|업글|연구|공격력", "프리이그나이터|예열",
        "hellion+upgrade|research|damage", "hellbat+upgrade|research|damage", "-변신",
    ),
    "research_neosteelframe": (
        "벙커+연구|업그레이드|업글|수용|더들어|늘려|튼튼", "네오스틸", "bunker+upgrade|capacity|research", "벙커+4명|더+많이",
    ),
    "research_personalcloaking": (
        "고스트|유령+은신|은시|투명|클로킹|은폐+가능|있게|연구|개발|업그레이드|업글|research|unlock",
        "ghost+cloak|invisible|stealth+research|unlock|upgrade|make", "개인은폐|퍼스널", "personal cloaking",
    ),
    "research_rapidfirelaunchers": (
        "사이클론+연구|업그레이드|업글|속사|연사|공속|공격속도+연구|올려|해줘|개발", "속사|연사+연구|업그레이드", "래피드+파이어+런처", "래피드파이어", "rapid+fire+launcher", "cyclone+research|upgrade|rapid|fire rate",
        "-락온|lock", "-뽑|생산|train",
    ),
    "research_ravencorvidreactor": (
        "레이븐|밤까마귀|까마귀+에너지+늘|올|연구|업그레이드|업글|많이|시작", "코르비드", "raven+energy|corvid|mana",
    ),
    "research_ravenrecalibratedexplosives": (
        "레이븐|밤까마귀|까마귀+폭발|폭약+연구|업그레이드|업글|강화|개발", "재조정|리캘리", "raven+explosive|recalibrated",
    ),
    "research_enhancedmunitions": (
        "레이븐|밤까마귀|까마귀+탄약|탄+연구|업그레이드|업글|강화|개발", "강화탄약", "raven+munitions|ammo|enhanced",
    ),
    "research_highcapacityfueltanks": (
        "연료+탱크|통|연구|업그레이드|업글", "우주공항+연료", "fuel+tank|research|upgrade", "바이킹|해방선|의료선+연료+연구",
    ),
    "research_advancedballistics": (
        "해방선+사거리|사정거리+늘|올|연구|업그레이드|업글|길게|멀리", "고급탄도|탄도학", "liberator+range", "advanced+ballistic",
    ),
    "research_smartservos": (
        "서보", "스마트+서보|서브", "변신+속도+연구|업그레이드|빠르|빨라", "화염기갑병|바이킹+변신+빠르|빨라|연구|업그레이드", "smart+servo",
        "transform+faster|speed+research|upgrade",
    ),
    "research_stimpack": (
        "스팀팩|스팀+연구|개발|업그레이드|업글|가능|있게|research|upgrade|unlock", "해병|불곰+체력|피+깎+연구|개발|업그레이드|업글|가능|있게",
        "해병+체력|피+깎+빨라|빠르|빨리+연구|개발|업그레이드|업글|가능|있게", "stim+research|upgrade|unlock|tech",
    ),
    # ------------------------------------------------------------------ train (17)
    "train_scv": ("일꾼+뽑|생산|계속|더|만들|늘", "scv+뽑|생산|계속|더|만들|늘", "worker+train|build|make|more", "건설로봇+뽑|생산|만들", "일꾼+많이|추가"),
    "train_marine": (
        "해병+뽑|생산|만들|추가|더|많이", "마린+뽑|생산|만들|추가|더|많이", "고기방패+해병|마린", "미네랄+보병|해병", "marine+train|build|make|more",
        "보병+뽑|생산|만들|추가|더|많이", "bio+more", "-공격력|방어력|업그레이드|업글|연구|방업|공업",
    ),
    "train_marauder": ("불곰+뽑|생산|만들|추가|더", "마라우더+뽑|생산", "marauder+train|build|make|more", "탱커+보병", "-슬로우|둔화|연구|업"),
    "train_reaper": ("사신+뽑|생산|만들|추가", "리퍼+뽑|생산", "reaper+train|build|make"),
    "train_ghost": ("유령+뽑|생산|만들|추가", "고스트+뽑|생산|만들", "ghost+train|build|make", "-핵|nuke|은신|cloak|연구"),
    "train_hellion": ("화염차+뽑|생산|만들|추가", "헬리온+뽑|생산", "hellion+train|build|make", "-변신|업그레이드|업글|연구"),
    "train_hellbat": ("화염기갑병+뽑|생산|만들|추가", "헬벳+뽑|생산|만들", "hellbat+train|build|make", "-변신|업그레이드|업글|연구", "두꺼운+화염차+뽑|생산"),
    "train_siegetank": (
        "탱크+뽑|생산|양산|만들|추가", "공성전차+뽑|생산|양산|만들", "tank+train|build|make|more", "-모드|시즈모드|시즈해|시즈로|시즈풀|시즈켜|시즈박|사거리|공격력|방어력",
    ),
    "train_widowmine": ("지뢰+뽑|생산|만들|추가", "위도우+마인+뽑|생산", "widow+train|build|make", "-매복|연구|업그레이드"),
    "train_cyclone": ("사이클론+뽑|생산|만들|추가", "cyclone+train|build|make", "-연구|업그레이드|업글|속사"),
    "train_thor": ("토르+뽑|생산|만들|추가", "thor+train|build|make", "-모드|공격력+올"),
    "train_medivac": ("의료선+뽑|생산|만들|추가", "메디박+뽑|생산", "medivac+train|build|make", "힐러+뽑|생산|만들", "-빠르|빨리|부스터|가속"),
    "train_vikingfighter": ("바이킹+뽑|생산|만들|추가", "viking+train|build|make", "-땅|착륙|날아|이륙|모드"),
    "train_liberator": ("해방선+뽑|생산|만들|추가", "liberator+train|build|make", "공중에서+지상+때리|공격+유닛+하나|뽑", "-모드|사거리|지키"),
    "train_banshee": ("밴시+뽑|생산|만들|추가", "banshee+train|build|make|more", "공중에서+지상+때리|공격+유닛+하나|뽑", "-은신|투명|클로킹|cloak|연구|빠르|속도|업"),
    "train_raven": ("밤까마귀+뽑|생산|만들|추가", "레이븐+뽑|생산|만들", "raven+train|build|make", "-에너지|폭발|탄약|연구"),
    "train_battlecruiser": ("전투순양함+뽑|생산|만들|추가", "배틀크루저+뽑|생산|만들", "battlecruiser+train|build|make", "bc+뽑|생산", "-야마토|연구"),
    # ------------------------------------------------------------------ build (16)
    "build_supplydepot": (
        "인구수+막|모자|부족|꽉|가득+서플|보급고|서플라이|depot|supply", "서플+지어|건설|올려|하나|추가|더", "보급고+지어|건설|하나|추가|더|올리",
        "인구수+늘|올|막혔|막힘|모자|부족", "supply+block|capped|depot|build", "-드롭|줘|내려|낮|올려+보급고",
    ),
    "build_barracks": ("병영+지어|건설|하나|추가|더|올리", "배럭+지어|건설|하나|추가|더|올리", "barracks+build|more|another", "rax+build|more"),
    "build_factory": ("군수공장+지어|건설|하나|추가|더|올리", "팩토리+지어|건설|하나|추가|더", "factory+build|more|another"),
    "build_starport": ("우주공항+지어|건설|하나|추가|더|올리", "스타포트+지어|건설|하나|추가|더", "starport+build|more|another"),
    "build_commandcenter": ("사령부+지어|건설|하나|추가|더|올리|먼저|우선", "앞마당+사령부", "멀티+지어|건설|먹|하나", "확장+하자|가자|해|지어|먹", "expand|expansion", "cc+build|expand", "-궤도|요새|이륙|날려"),
    "build_engineeringbay": ("공학+연구소+지어|건설|하나", "이베이+지어|건설|하나", "에베+지어|건설|하나|추가", "엔지니어링+베이+지어|건설|하나|추가", "엔지니어링베이+지어|건설", "-사령부|병영|벙커|배럭", "engineering+bay+build"),
    "build_armory": ("무기고+지어|건설|하나|추가", "armory+build"),
    "build_ghostacademy": ("유령+사관학교+지어|건설|하나", "고스트+아카데미+지어|건설", "ghost+academy+build"),
    "build_fusioncore": ("융합로+지어|건설|하나", "퓨전+코어+지어|건설", "fusion+core+build"),
    "build_bunker": ("벙커+지어|건설|하나|추가|더|올리", "bunker+build|more", "-연구|업그레이드|해체|철거|환급"),
    "build_missileturret": (
        "터렛+지어|건설|올려|하나|세워|설치+대공|방어", "포탑+지어|건설|하나|세워", "대공+방어+터렛|포탑|지어|건설", "turret+build|anti-air|defend",
        "미사일+터렛+지어|건설|세워", "-사거리|연구|업그레이드",
    ),
    "build_sensortower": ("감시탑+지어|건설|하나|세워", "센서+타워+지어|건설", "sensor+tower+build", "레이더+지어|건설|세워"),
    "build_refinery": (
        "가스+캐|채취|건물|지어|건설|정제소", "정제소+지어|건설|하나", "refinery+build", "gas+build|mine|harvest", "가스통+지어|건설", "가스+먹|뽑아야",
    ),
    "build_techlab": (
        "기술실|테크랩+달|붙|지어|건설|해줘", "병영|군수공장|우주공항+기술실|테크랩", "tech+lab+build|add|attach", "상위+유닛+가능+애드온",
        "-연구|업그레이드",
    ),
    "build_reactor": (
        "리액터|반응로+달|붙|지어|건설|해줘", "반응로", "병영|군수공장|우주공항+리액터", "reactor+build|add|attach", "동시+생산|두배|2기씩+애드온|달|붙", "쌍둥이|더블+생산",
        "-연구|업그레이드|에너지",
    ),
    "build_nuke": ("핵+준비|장전|만들|생산|장착", "핵+미사일+준비|만들|장전", "고스트|유령+핵+준비|장전", "nuke+arm|prepare|build|load|ready", "사일로+핵", "-발사|쏴|투하|떨"),
    # ------------------------------------------------------------------ morph (14)
    "morph_siegemode": (
        "탱크+포대|시즈|공성|고정|박아|고정사격|자리잡|배치|siege", "시즈+모드|해줘|켜|전환|siege", "탱크+사거리|사정거리|멀리+늘|길게|올|키|크게|넓|해줘", "tank+range+increase|longer|more", "tank+siege|deploy|fixed|sieged",
        "-풀|해제|이동|돌려|unsiege|일어나|기동",
        "-탱크뽑|탱크생산|탱크양산|탱크만들|탱크추가|탱크더뽑|탱크더만들", "-trainasiege|buildasiege|makeasiege|trainsiege|buildsiege|makesiege|traintank|buildtank|maketank|trainatank|buildatank|makeatank",
    ),
    "morph_unsiege": (
        "탱크+이동|일어나|풀|해제|기동|걸어|돌려+모드|시즈|해줘|해|돌려", "시즈+풀|해제|꺼|끄", "탱크+이동+모드", "unsiege|un-siege", "tank+mobile|move+mode|unsiege|unsieg",
        "-탱크뽑|탱크생산|탱크양산|탱크만들|탱크추가|탱크더뽑|탱크더만들",
    ),
    "morph_orbitalcommand": ("사령부+궤도|오비탈", "궤도+사령부+바꿔|변환|업그레이드|전환|해줘|만들|올려|올리|업글", "orbital", "스캔|뮬+쓰려고+사령부+업", "사령부+업그레이드+궤도"),
    "morph_planetaryfortress": ("사령부+요새|행성|플랭|방어", "행성+요새", "planetary", "사령부+방어+요새", "pf+만들|변환|바꿔"),
    "morph_supplydepot_lower": (
        "보급고+낮|내려|열어|열|길+열|통과", "서플+낮|내려|열어|통과", "depot+lower|open|down", "서플+길+열", "-올려|막|세워",
    ),
    "morph_supplydepot_raise": ("보급고+올려|막|세워|닫|벽", "서플+올려|막|세워|닫|벽", "depot+raise|close|wall|up", "입구+막아+보급고|서플|벽", "-낮|내려|열"),
    "morph_hellbat": (
        "화염차+변신|두꺼|두껍|탱커|벳|헬벳|기갑병|모드|바꿔", "화염차+헬벳", "hellion+hellbat|morph|transform|thick", "hellbat+mode|morph|transform",
        "-뽑|생산|연구|업", "-화염차로|헬리온으로|화염차모드로",
    ),
    "morph_hellion": ("화염기갑병|헬벳+화염차로|원래|되돌|변신|바꿔|헬리온+모드", "화염차로+변신|바꿔|되돌|돌려|전환|해줘", "헬리온으로+변신|바꿔|전환", "hellbat+hellion|back|revert", "-뽑|생산|연구|업"),
    "morph_vikingassaultmode": (
        "바이킹+땅|착륙|내려|지상|돌격|걸어|뛰어", "viking+land|ground|assault|walk", "바이킹+땅에+내려", "-다시+날|이륙|공중|전투기",
    ),
    "morph_vikingfightermode": ("바이킹+다시+날|이륙|공중|전투기|하늘|떠|올라", "바이킹+날아|날개|비행", "viking+fly|air|fighter|takeoff|up", "바이킹+전투기+모드"),
    "morph_liberatoragmode": (
        "해방선+땅|지키|지상|방어|구역|자리|고정|수비|모드|AG|원|서클", "liberator+defend|ground|zone|siege|ag", "해방선+원+치|그려|설치|고정", "-전투기|공중|대공|aa",
    ),
    "morph_liberatoraamode": ("해방선+공중|대공|전투기|다시+날|aa|하늘|풀|해제", "liberator+air|fighter|aa|fly", "해방선+전투기+모드|전환", "-땅|지키|지상|ag"),
    "morph_thorhighimpactmode": (
        "토르+고충격|단일|한놈|한마리|대공+단일|비행기+모드", "토르+고충격+페이로드|모드", "thor+high impact|single|one target", "토르+하이임팩트",
    ),
    "morph_thorexplosivemode": (
        "토르+폭발|범위|광역|스플래시+모드|전환|페이로드|대공", "토르+기본|일반+모드", "thor+explosive|splash|aoe|normal|mode", "토르+뭉친+공중",
    ),
    # ------------------------------------------------------------------ effect / behavior / other
    "effect_scan": (
        "스캔|스캐너|스윕", "적+본진|앞마당|기지|병력|멀티+보자|보여|확인|살펴|궁금|뭐있", "상대+뭐|뭘+하는지|보고싶|보자|확인", "scan|scanner|reveal|sweep",
        "look+enemy|base|main", "정보+보게+궤도|스캔",
    ),
    "effect_calldownmule": (
        "뮬|뮤마|뮤울|mule", "뮬+떨궈|떨어|내려|써|쓰|뿌려|쏴|호출|불러|소환|해줘|줘", "mule+call|drop|use|cast", "미네랄+더|많이|빨리|빠르게|늘리|캐게|캐|추가|수입", "자원+더|많이|빨리|캐게|늘리|수입|채취", "income+more|boost|faster", "mineral+more|faster|mine",
        "일꾼+임시|호출+자원",
    ),
    "effect_supplydrop": (
        "보급+드롭|줘|내려|떨궈|주|받|쏴", "서플+드롭|떨궈|내려줘", "보급+투하|투입", "인구수+모자|부족|막혔|꽉+보급+줘|드롭|내려|떨궈|주", "supply+drop|call down|extra", "인구수+늘려줘+궤도|사령부|에너지",
        "-지어|건설|하나|올려|낮",
    ),
    "lift": (
        "사령부|병영|군수공장|우주공항|건물+이륙|띄워|띄우|날려|날아|뜨게|공중|올려", "lift+off|lift|takeoff|fly|float", "건물+날려|띄워|이륙|하늘", "사령부+날려|날아|띄워+앞마당|멀티|가", "-착륙|내려앉|내려안|land", "-궤도로|궤도사령부로|오비탈로|요새로|행성요새로",
    ),
    "land": ("착륙|내려앉|내려안|내려놓|land", "건물+착륙|내려|앉혀|놓아|내려앉", "앞마당|본진|멀티+착륙|내려앉", "-이륙|띄워|날려"),
    "effect_stim": (
        "스팀팩|스팀|전투자극|자극제", "해병|불곰+피|체력+깎|빨라|빠르|빨리|공속", "보병+빨라지|속도|공속+피+깎", "stim|stimpack", "해병+몸+던|돌격+피+깎", _NOT_RESEARCH,
    ),
    "effect_emp": ("emp|이엠피|전자기|충격파", "에너지+날려|제거|빼|없애|태워|지워", "보호막+날려|제거|없애|벗겨|빼", "유령+에너지|보호막", "protoss+shield+remove|drain"),
    "effect_ghostsnipe": ("저격|스나이프|snipe", "유령+생체+죽여|저격|한방|채널", "고스트+저격|죽여|쏴", "ghost+snipe|kill"),
    "effect_nukecalldown": ("핵+발사|쏴|투하|떨궈|날려|터뜨려|폭파", "전술핵+발사|쏴|투하|날려", "nuke+launch|fire|drop|call|strike", "핵+적+본진|앞마당|병력|떨", "-준비|장전|만들|생산|장착"),
    "effect_kd8charge": ("kd8|케이디8|케이디에이트", "사신+폭탄|수류탄|던져|던지", "리퍼+폭탄|수류탄|던져", "reaper+grenade|bomb|throw|charge", "폭탄+던져|투척"),
    "effect_lockon": ("락온|록온|lockon|lock", "사이클론+고정|묶어|물고|따라가|쏴|락|한놈", "cyclone+lock|target|fire", "사이클론+공격+지정"),
    "effect_hunterseekermissile": (
        "추적+미사일|탄", "헌터+시커|씨커", "사냥꾼+미사일", "시커+미사일", "레이븐|밤까마귀+미사일+쏴|발사|날려", "raven+missile|seeker|hunter", "-연구|탄약|폭발|업",
    ),
    "effect_pointdefensedrone": ("방어+드론|드론+설치|깔|놔", "포인트+디펜스", "요격+드론|드론", "레이븐|밤까마귀+드론", "pdd|point defense|defense drone"),
    "effect_autoturret": (
        "자동+터렛|포탑+설치|깔|놔|세워|소환", "오토+터렛", "레이븐|밤까마귀+터렛+설치|깔|놔|소환|세워", "auto+turret|autoturret", "autoturret", "-건설|지어|하나+병영|공학",
    ),
    "effect_heal": ("치료|힐|heal", "의료선|메디박|메딕+치료|힐|고쳐|회복|살려", "해병|보병+치료|회복|힐|살려", "medivac+heal|treat"),
    "effect_medivacigniteafterburners": (
        "의료선|메디박|메딕+빠르|빨리|빨라|부스터|가속|속도|스피드|달려|급하게|부스트", "medivac+fast|faster|boost|speed|afterburner", "부스터+켜|써|가동", "afterburner", "-뽑|생산|연구|업|train",
    ),
    "effect_tacticaljump": (
        "전술+도약|점프|워프", "도약|점프+전투순양함|bc|배틀크루저", "bc+점프|도약|순간이동|jump|warp|teleport", "배틀크루저|전투순양함+이동+순간|워프", "tactical+jump|warp", "-연구",
    ),
    "effect_yamatogun": (
        "야마토+쏴|발사|써|사용|때려|맞춰|공격|fire|shoot|use", "bc+야마토+쏴|써|발사", "yamato+fire|shoot|cast|use", "-연구|가능|있게|개발|업그레이드|research|unlock",
    ),
    "effect_widowmineattack": (
        "지뢰+발사|쏴|공격|때려|터뜨려|폭발|공격해", "매복+지뢰+쏴|발사|공격", "mine+fire|shoot|attack|detonate", "widow+attack|fire|shoot", "-연구|뽑|생산|매복해|풀|땅속",
    ),
    "effect_repair": ("수리|고쳐|repair|fix", "scv+수리|고쳐|고치", "건물|탱크|벙커+수리|고쳐|고치|수리해", "일꾼+고쳐|수리"),
    "effect_salvage": ("벙커+해체|철거|환급|부수|팔|없애|허물|salvage|sell|remove", "salvage|sell+bunker"),
    "effect_spray": ("스프레이|낙서|spray", "그림+뿌|그려|낙서", "로고+뿌|그려|찍|스프레이"),
    "behavior_cloakon": (
        "클로킹+켜|써|해|들어가|가동|시작|on", "은신+켜|써|해|들어|시작|하자|on", "투명+해|되|켜|들어|시작", "은폐+켜|써|해|들어|시작", "cloak+on|activate|enable|turn on|use",
        "밴시|유령|고스트+은신|클로킹|투명+해|켜|써", "banshee|ghost+cloak", "banshees|ghosts+cloak", "-연구|가능|개발|업그레이드|풀|해제|꺼|끄|off|research", "-연구",
    ),
    "behavior_cloakoff": (
        "클로킹+풀|해제|꺼|끄|취소|off", "은신+풀|해제|꺼|끄|취소|벗|off", "투명+풀|해제|꺼|끄|벗|취소", "은폐+풀|해제|꺼|끄", "decloak|cloak+off|disable|turn off",
    ),
    "behavior_holdfireon": (
        "사격+중지|멈춰|금지|하지마|그만|stop|hold", "발사+금지|하지마|멈춰|금지", "홀드+파이어|사격", "hold fire", "fire+stop|hold|off|disable", "공격+하지마+유령|고스트|들키|안",
        "-재개|허용|해제|풀|다시|off|release|resume",
    ),
    "behavior_holdfireoff": (
        "사격+재개|허용|해제|풀|다시|가능|풀어|열어", "발사+재개|허용|다시|풀", "홀드+파이어+해제|풀|끄|끔", "release+fire|hold fire|resume|fire at will", "자유+사격|발사|공격", "공격+다시+해|재개|허용",
    ),
    "burrowdown": (
        "지뢰+매복|땅속|숨|박아|묻|묻어|들어가|파고|파묻|박", "지뢰+바닥|땅+숨|들어|박", "widow+burrow|hide|dig", "mine+burrow|hide|dig|ambush", "-올라|풀|해제|나와|unburrow|up",
    ),
    "burrowup": ("지뢰+올라|나와|풀|해제|깨|일어|뽑|꺼내|unburrow|up|surface", "매복+풀|해제|끝", "widow+unburrow|up|surface|emerge", "mine+unburrow|up|surface|emerge"),
    "load": (
        "태워|태우|탑승|실어|싣|타|load", "수송+적재|탑승|태워|싣", "의료선+해병|병력|유닛|보병+태워|태우|실어|싣", "벙커+넣어|들어가|입장", "load+unit|bunker|medivac|marine", "-전부|모두|다+내려|내리",
    ),
    "unloadallat": (
        "의료선+내려|내리|하차|투하|드롭+지정|여기|거기|위치|적|본진|앞마당", "의료선+자리+내려|내리|하차|투하", "드롭+내려|투하|하차|뿌려", "medivac+unload|drop|deploy", "병력+내려+의료선", "unload+at|here|there", "견제+드롭",
    ),
    "unloadall": (
        "전부|모두|다+내려|내리|하차|비워|꺼내|풀어|unload", "벙커+비워|꺼내|내려|내리", "일꾼+내려|내리|꺼내|나와+사령부", "unload+all|everything|bunker", "empty+bunker",
    ),
    "loadall": (
        "일꾼+대피|넣어|태워|피신|숨겨|수용|들어가+사령부", "사령부+일꾼+넣어|태워|수용|대피|피신", "scv+evacuate|load|hide|shelter", "evacuate+workers|scv", "load all|loadall", "-내려|내리|꺼내|비워",
    ),
}
