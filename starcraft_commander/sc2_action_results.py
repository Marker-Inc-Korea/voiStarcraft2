"""SC2 ``ActionResult`` vocabulary and per-order game-response tracking.

SC2 rejects orders in two channels (see ``docs/sc2-action-results.md``):

* ``ResponseAction.result`` - one ``ActionResult`` per submitted ``Action`` in the
  ``RequestAction`` reply (``Success`` or an error code).
* ``ResponseObservation.action_errors`` - ``ActionError{unit_tag, ability_id, result}``
  entries, the "red text" the game UI would show.

Nothing in this module needs StarCraft II, python-sc2 or ``s2clientprotocol`` at import
time: codes are plain ints and the vocabulary is keyed by the protocol *names*.  A test
enumerates the installed ``error_pb2.ActionResult`` and fails on any value this module
cannot describe, so a protocol upgrade cannot silently add unknowns.

``accepted`` is tri-state everywhere: ``True`` only for an explicit ``Success`` result,
``False`` for an explicit error result, ``None`` when no response was seen (unknown).
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

# --------------------------------------------------------------------------- codes
# Mirrors s2clientprotocol/error.proto ``ActionResult`` (names are what the test pins).
# Only the names this module needs to *recognise* by number live in the generated map
# below; the authoritative number->name table is built from the protocol when available.

SUCCESS_CODE: Final[int] = 1

CATEGORIES: Final[frozenset[str]] = frozenset(
    {
        "success",
        "resources",
        "supply",
        "energy",
        "queue",
        "placement",
        "tech",
        "target",
        "range",
        "busy",
        "unit_state",
        "cargo",
        "ability",
        "other",
    }
)


@dataclass(frozen=True)
class ActionResultInfo:
    """Stable, UI-ready description of one ``ActionResult`` value."""

    code: int
    name: str
    category: str
    reason_ko: str
    reason_en: str
    user_fixable: bool
    retryable: bool
    known: bool = True
    """False only for a code/name this module has no mapping for (generic fallback)."""

    @property
    def accepted(self) -> bool:
        return self.code == SUCCESS_CODE

    def to_game_result(self) -> dict[str, object]:
        """The ``game_result`` evidence mapping for this single result."""

        return {
            "accepted": self.accepted,
            "code": self.code,
            "name": self.name,
            "category": self.category,
            "reason_ko": self.reason_ko,
            "reason_en": self.reason_en,
            "user_fixable": self.user_fixable,
            "retryable": self.retryable,
        }


# category, ko, en, user_fixable, retryable
_Row = tuple[str, str, str, bool, bool]

_EXPLICIT: Final[dict[str, _Row]] = {
    "Success": ("success", "게임이 주문을 받아들였습니다", "the game accepted the order", False, False),
    "NotSupported": ("ability", "이 주문은 지원되지 않습니다", "the order is not supported", False, False),
    "Error": ("other", "게임이 주문을 거부했습니다 (구체적 이유 없음)", "the game rejected the order (no specific reason)", False, False),
    "CantQueueThatOrder": ("queue", "그 주문은 대기열에 넣을 수 없습니다", "that order cannot be queued", True, False),
    "Retry": ("other", "게임이 잠시 후 다시 시도하라고 합니다", "the game asked to retry the order", False, True),
    "Cooldown": ("busy", "재사용 대기시간 중입니다", "the ability is on cooldown", True, True),
    "QueueIsFull": ("queue", "생산 대기열이 가득 찼습니다", "the production queue is full", True, True),
    "RallyQueueIsFull": ("queue", "집결 지점 대기열이 가득 찼습니다", "the rally queue is full", True, False),
    "FoodUsageImpossible": ("supply", "인구수 제한 때문에 만들 수 없습니다", "the supply usage is impossible", True, False),
    "NotEnoughCharges": ("energy", "사용 가능한 충전 횟수가 없습니다", "no charges are available", True, True),
    "CantAddMoreCharges": ("energy", "충전을 더 추가할 수 없습니다", "no more charges can be added", True, False),
    "CantTrade": ("resources", "자원을 교환할 수 없습니다", "resources cannot be traded", False, False),
    "CantSpend": ("resources", "자원을 사용할 수 없습니다", "the resources cannot be spent", False, False),
    "CouldntAllocateUnit": ("unit_state", "명령을 수행할 유닛을 배정하지 못했습니다", "no unit could be allocated for the order", True, True),
    "UnitCantMove": ("unit_state", "그 유닛은 움직일 수 없습니다", "the unit cannot move", True, False),
    "TransportIsHoldingPosition": ("cargo", "수송선이 위치 고수 중입니다", "the transport is holding position", True, False),
    "BuildTechRequirementsNotMet": ("tech", "건설에 필요한 선행 건물/기술이 없습니다", "the building's tech requirements are not met", True, False),
    "CantFindPlacementLocation": ("placement", "건물을 놓을 위치를 찾지 못했습니다", "no placement location could be found", True, True),
    "CantBuildOnThat": ("placement", "그 위에는 건설할 수 없습니다", "cannot build on that", True, False),
    "CantBuildTooCloseToDropOff": ("placement", "자원 반납 건물에 너무 가까워 건설할 수 없습니다", "too close to a resource drop-off to build", True, False),
    "CantBuildLocationInvalid": ("placement", "건설 위치가 올바르지 않습니다", "the build location is invalid", True, False),
    "CantSeeBuildLocation": ("placement", "건설 위치가 보이지 않습니다 (시야 없음)", "the build location is not visible", True, True),
    "CantBuildTooCloseToCreepSource": ("placement", "점막 근원에 너무 가까워 건설할 수 없습니다", "too close to a creep source to build", True, False),
    "CantBuildTooCloseToResources": ("placement", "자원에 너무 가까워 건설할 수 없습니다", "too close to resources to build", True, False),
    "CantBuildTooFarFromWater": ("placement", "물에서 너무 멀어 건설할 수 없습니다", "too far from water to build", True, False),
    "CantBuildTooFarFromCreepSource": ("placement", "점막에서 너무 멀어 건설할 수 없습니다", "too far from a creep source to build", True, False),
    "CantBuildTooFarFromBuildPowerSource": ("placement", "건설 에너지원(파일런 등)에서 너무 멀어 건설할 수 없습니다", "too far from a build power source (pylon) to build", True, False),
    "CantBuildOnDenseTerrain": ("placement", "지형이 건설하기에 적합하지 않습니다", "the terrain is too dense to build on", True, False),
    "CantTrainTooFarFromTrainPowerSource": ("placement", "생산 에너지원(파일런 등)에서 너무 멀어 생산할 수 없습니다", "too far from a train power source to train", True, False),
    "CantLandLocationInvalid": ("placement", "착륙 위치가 올바르지 않습니다", "the landing location is invalid", True, False),
    "CantSeeLandLocation": ("placement", "착륙 위치가 보이지 않습니다", "the landing location is not visible", True, True),
    "CantLandTooCloseToCreepSource": ("placement", "점막 근원에 너무 가까워 착륙할 수 없습니다", "too close to a creep source to land", True, False),
    "CantLandTooCloseToResources": ("placement", "자원에 너무 가까워 착륙할 수 없습니다", "too close to resources to land", True, False),
    "CantLandTooFarFromWater": ("placement", "물에서 너무 멀어 착륙할 수 없습니다", "too far from water to land", True, False),
    "CantLandTooFarFromCreepSource": ("placement", "점막에서 너무 멀어 착륙할 수 없습니다", "too far from a creep source to land", True, False),
    "CantLandTooFarFromBuildPowerSource": ("placement", "건설 에너지원에서 너무 멀어 착륙할 수 없습니다", "too far from a build power source to land", True, False),
    "CantLandTooFarFromTrainPowerSource": ("placement", "생산 에너지원에서 너무 멀어 착륙할 수 없습니다", "too far from a train power source to land", True, False),
    "CantLandOnDenseTerrain": ("placement", "지형이 착륙하기에 적합하지 않습니다", "the terrain is too dense to land on", True, False),
    "AddOnTooFarFromBuilding": ("placement", "애드온 자리가 건물에서 너무 멀거나 막혀 있습니다", "the add-on is too far from (or blocked next to) the building", True, False),
    "MustBuildRefineryFirst": ("tech", "먼저 정제소를 지어야 합니다", "a refinery must be built first", True, False),
    "BuildingIsUnderConstruction": ("busy", "건물이 아직 건설 중입니다", "the building is still under construction", True, True),
    "CantFindDropOff": ("target", "자원을 반납할 건물을 찾지 못했습니다", "no drop-off building could be found", True, False),
    "CantLoadOtherPlayersUnits": ("cargo", "다른 플레이어의 유닛은 태울 수 없습니다", "cannot load another player's units", False, False),
    "NotEnoughRoomToLoadUnit": ("cargo", "탑승 공간이 부족합니다", "not enough room to load the unit", True, False),
    "CantUnloadUnitsThere": ("cargo", "그 위치에는 유닛을 내릴 수 없습니다", "cannot unload units there", True, False),
    "CantWarpInUnitsThere": ("placement", "그 위치에는 유닛을 소환할 수 없습니다", "cannot warp in units there", True, False),
    "CantLoadImmobileUnits": ("cargo", "움직일 수 없는 유닛은 태울 수 없습니다", "cannot load immobile units", False, False),
    "CantRechargeImmobileUnits": ("energy", "움직일 수 없는 유닛은 충전할 수 없습니다", "cannot recharge immobile units", False, False),
    "CantRechargeUnderConstructionUnits": ("energy", "건설 중인 유닛은 충전할 수 없습니다", "cannot recharge units under construction", False, False),
    "CantLoadThatUnit": ("cargo", "그 유닛은 태울 수 없습니다", "that unit cannot be loaded", False, False),
    "NoCargoToUnload": ("cargo", "내릴 유닛이 없습니다", "there is no cargo to unload", True, False),
    "LoadAllNoTargetsFound": ("cargo", "태울 수 있는 유닛을 찾지 못했습니다", "no loadable targets were found", True, False),
    "NotWhileOccupied": ("busy", "유닛이 다른 일을 하는 중이라 지금은 할 수 없습니다", "not possible while the unit is occupied", True, True),
    "CantAttackWithoutAmmo": ("unit_state", "탄약이 없어 공격할 수 없습니다", "cannot attack without ammo", True, False),
    "CantHoldAnyMoreAmmo": ("unit_state", "탄약을 더 보유할 수 없습니다", "cannot hold any more ammo", True, False),
    "TechRequirementsNotMet": ("tech", "필요한 선행 건물/업그레이드가 없습니다", "tech requirements are not met", True, False),
    "MustLockdownUnitFirst": ("target", "먼저 유닛을 록다운해야 합니다", "the unit must be locked down first", True, False),
    "YouCantControlThatUnit": ("unit_state", "그 유닛은 조종할 수 없습니다", "you cannot control that unit", False, False),
    "YouCantIssueCommandsToThatUnit": ("unit_state", "그 유닛에는 명령을 내릴 수 없습니다", "you cannot issue commands to that unit", False, False),
    "RequiresHealTarget": ("target", "치료할 대상이 필요합니다", "a heal target is required", True, False),
    "RequiresRepairTarget": ("target", "수리할 대상이 필요합니다", "a repair target is required", True, False),
    "NoItemsToDrop": ("other", "떨어뜨릴 아이템이 없습니다", "there are no items to drop", False, False),
    "CantHoldAnyMoreItems": ("other", "아이템을 더 가질 수 없습니다", "cannot hold any more items", False, False),
    "CantHoldThat": ("other", "그것은 가질 수 없습니다", "cannot hold that", False, False),
    "TargetHasNoInventory": ("target", "대상에게 인벤토리가 없습니다", "the target has no inventory", False, False),
    "CantDropThisItem": ("other", "이 아이템은 버릴 수 없습니다", "cannot drop this item", False, False),
    "CantMoveThisItem": ("other", "이 아이템은 옮길 수 없습니다", "cannot move this item", False, False),
    "CantPawnThisUnit": ("other", "이 유닛은 처분할 수 없습니다", "cannot pawn this unit", False, False),
    "CantRechargeOtherPlayersUnits": ("energy", "다른 플레이어의 유닛은 충전할 수 없습니다", "cannot recharge another player's units", False, False),
    "CantLockdown": ("target", "그 대상은 록다운할 수 없습니다", "that target cannot be locked down", True, False),
    "CantMindControl": ("target", "그 대상은 정신 지배할 수 없습니다", "that target cannot be mind controlled", True, False),
    "NoCalldownAvailable": ("busy", "사용할 수 있는 지원 호출이 없습니다", "no calldown is available", True, True),
    "WaypointListFull": ("queue", "경유지 목록이 가득 찼습니다", "the waypoint list is full", True, False),
    "CantFindEnoughTargets": ("target", "대상을 충분히 찾지 못했습니다", "not enough targets were found", True, False),
    "AlreadySpawningLarva": ("busy", "이미 애벌레를 생성하는 중입니다", "already spawning larva", True, True),
    "CantUseMinimap": ("other", "미니맵으로는 이 명령을 내릴 수 없습니다", "cannot be used on the minimap", False, False),
    "CantUseInfoPanel": ("other", "정보 패널로는 이 명령을 내릴 수 없습니다", "cannot be used from the info panel", False, False),
    "OrderQueueIsFull": ("queue", "명령 대기열이 가득 찼습니다", "the order queue is full", True, True),
    "CantHarvestThatResource": ("target", "그 자원은 채취할 수 없습니다", "that resource cannot be harvested", True, False),
    "HarvestersNotRequired": ("target", "채취 일꾼이 더 필요하지 않습니다", "no more harvesters are required", True, False),
    "AlreadyTargeted": ("target", "이미 같은 대상으로 지정되어 있습니다", "already targeted", True, False),
    "CantAttackWeaponsDisabled": ("unit_state", "무기가 비활성화되어 공격할 수 없습니다", "cannot attack while weapons are disabled", True, True),
    "CouldntReachTarget": ("range", "대상에게 도달할 수 없습니다", "the target could not be reached", True, False),
    "TargetIsOutOfRange": ("range", "대상이 사거리 밖에 있습니다", "the target is out of range", True, True),
    "TargetIsTooClose": ("range", "대상이 너무 가깝습니다", "the target is too close", True, True),
    "TargetIsOutOfArc": ("range", "대상이 사격 각도 밖에 있습니다", "the target is outside the firing arc", True, True),
    "CantFindTeleportLocation": ("placement", "순간이동할 위치를 찾지 못했습니다", "no teleport location could be found", True, False),
    "InvalidItemClass": ("other", "아이템 종류가 올바르지 않습니다", "invalid item class", False, False),
    "CantFindCancelOrder": ("other", "취소할 주문을 찾지 못했습니다", "no order to cancel was found", True, False),
}

# NotEnough{X} / TooMuch{X} / {X}Suppressed.  noun_ko, category, user_fixable_for_not_enough
_RESOURCES: Final[dict[str, tuple[str, str, str, bool]]] = {
    "Minerals": ("미네랄", "minerals", "resources", True),
    "Vespene": ("가스", "vespene gas", "resources", True),
    "Terrazine": ("테라진", "terrazine", "resources", True),
    "Custom": ("특수 자원", "custom resource", "resources", True),
    "Food": ("인구수(보급)", "supply", "supply", True),
    "Life": ("생명력", "life", "unit_state", False),
    "Shields": ("보호막", "shields", "unit_state", False),
    "Energy": ("에너지", "energy", "energy", True),
}

_TARGET_NOUNS: Final[dict[str, tuple[str, str]]] = {
    "AirUnits": ("공중 유닛", "air units"),
    "ArmoredUnits": ("중장갑 유닛", "armored units"),
    "BiologicalUnits": ("생체 유닛", "biological units"),
    "BuriedUnits": ("매복(잠복) 유닛", "buried units"),
    "Caster": ("시전자 자신", "the caster"),
    "CloakedUnits": ("은폐된 유닛", "cloaked units"),
    "DazedUnits": ("혼란 상태 유닛", "dazed units"),
    "DeadUnits": ("죽은 유닛", "dead units"),
    "Destructibles": ("파괴 가능한 지형물", "destructibles"),
    "DetectedUnits": ("탐지된 유닛", "detected units"),
    "EnemyUnits": ("적 유닛", "enemy units"),
    "EnergyCapableUnits": ("에너지를 쓰는 유닛", "energy-capable units"),
    "Flyers": ("비행 유닛", "flyers"),
    "FriendlyUnits": ("아군 유닛", "friendly units"),
    "GroundUnits": ("지상 유닛", "ground units"),
    "Hallucinations": ("환상 유닛", "hallucinations"),
    "HeroicUnits": ("영웅 유닛", "heroic units"),
    "HiddenUnits": ("숨겨진 유닛", "hidden units"),
    "InvulnerableUnits": ("무적 유닛", "invulnerable units"),
    "Items": ("아이템", "items"),
    "LightUnits": ("경장갑 유닛", "light units"),
    "MassiveUnits": ("거대 유닛", "massive units"),
    "MechanicalUnits": ("기계 유닛", "mechanical units"),
    "Missile": ("투사체", "missiles"),
    "NeutralUnits": ("중립 유닛", "neutral units"),
    "Outer": ("바깥쪽 유닛", "outer units"),
    "PassiveUnits": ("비활성 유닛", "passive units"),
    "PreventDefeatUnits": ("패배 방지 유닛", "prevent-defeat units"),
    "PreventRevealUnits": ("공개 방지 유닛", "prevent-reveal units"),
    "PsionicUnits": ("사이오닉 유닛", "psionic units"),
    "Race": ("특정 종족의 유닛", "units of a specific race"),
    "ResistantUnits": ("저항 유닛", "resistant units"),
    "RevivableUnits": ("부활 가능한 유닛", "revivable units"),
    "RoboticUnits": ("로봇 유닛", "robotic units"),
    "ShieldCapableUnits": ("보호막을 가진 유닛", "shield-capable units"),
    "SimilarUnits": ("같은 종류의 유닛", "similar units"),
    "Structures": ("건물", "structures"),
    "StunnedUnits": ("기절한 유닛", "stunned units"),
    "SummonedUnits": ("소환된 유닛", "summoned units"),
    "UncommandableUnits": ("명령 불가 유닛", "uncommandable units"),
    "UnderConstructionUnits": ("건설 중인 유닛", "units under construction"),
    "UnitWithEnergy": ("에너지가 있는 유닛", "units with energy"),
    "UnitWithShields": ("보호막이 있는 유닛", "units with shields"),
    "UnitsInAStasisField": ("시간 정지장 안의 유닛", "units in a stasis field"),
    "UnstoppableUnits": ("저지 불가 유닛", "unstoppable units"),
    "User1": ("지정 속성 유닛", "user-flag units"),
    "WorkerUnits": ("일꾼", "worker units"),
    "YourOwnUnits": ("내 유닛", "your own units"),
    # MustTarget* only
    "Inventory": ("인벤토리가 있는 대상", "an inventory"),
    "PawnableUnit": ("처분 가능한 유닛", "a pawnable unit"),
    "Resources": ("자원", "resources"),
    "Unit": ("유닛", "a unit"),
    "UnitWithLife": ("생명력이 있는 유닛", "a unit with life"),
    "VisibleLocation": ("보이는 위치", "a visible location"),
    "VisibleUnit": ("보이는 유닛", "a visible unit"),
    "WalkableLocation": ("걸어갈 수 있는 위치", "a walkable location"),
    # CantTarget* only
    "ExhaustedResources": ("고갈된 자원", "exhausted resources"),
    "ThatUnit": ("그 유닛", "that unit"),
}


def _resolve_by_name(name: str) -> _Row | None:
    row = _EXPLICIT.get(name)
    if row is not None:
        return row
    for prefix, template in (("NotEnough", "not_enough"), ("TooMuch", "too_much")):
        if name.startswith(prefix):
            spec = _RESOURCES.get(name[len(prefix) :])
            if spec is None:
                return None
            noun_ko, noun_en, category, fixable = spec
            if template == "not_enough":
                return (category, f"{noun_ko}이(가) 부족합니다", f"not enough {noun_en}", fixable, category in {"energy", "unit_state", "supply", "resources"})
            return (category, f"{noun_ko}이(가) 이미 가득 찼습니다", f"too much {noun_en}", False, False)
    if name.endswith("Suppressed"):
        spec = _RESOURCES.get(name[: -len("Suppressed")])
        if spec is None:
            return None
        noun_ko, noun_en, category, _ = spec
        return (category, f"{noun_ko}이(가) 억제되어 사용할 수 없습니다", f"{noun_en} is suppressed", False, True)
    for prefix, must in (("MustTarget", True), ("CantTarget", False)):
        if name.startswith(prefix):
            nouns = _TARGET_NOUNS.get(name[len(prefix) :])
            if nouns is None:
                return None
            noun_ko, noun_en = nouns
            if must:
                return ("target", f"대상이 올바르지 않습니다 — {noun_ko}만 대상으로 지정할 수 있습니다", f"invalid target: must target {noun_en}", True, False)
            return ("target", f"대상이 올바르지 않습니다 — {noun_ko}은(는) 대상으로 지정할 수 없습니다", f"invalid target: cannot target {noun_en}", True, False)
    return None


def _protocol_names() -> dict[int, str]:
    """Number -> name from the installed protocol; empty when it is not installed."""

    try:
        from s2clientprotocol import error_pb2
    except ImportError:  # pragma: no cover - environment dependent
        return {}
    return {int(value): str(name) for name, value in error_pb2.ActionResult.items()}


_NAMES_BY_CODE: dict[int, str] | None = None


def action_result_name(code: int) -> str | None:
    """Protocol name of ``code`` or ``None`` when the installed protocol lacks it."""

    global _NAMES_BY_CODE
    if _NAMES_BY_CODE is None:
        _NAMES_BY_CODE = _protocol_names()
    return _NAMES_BY_CODE.get(int(code))


def describe_action_result_name(name: str, code: int) -> ActionResultInfo:
    """Describe a result by protocol *name*; unmapped names get ``known=False``."""

    row = _resolve_by_name(name)
    if row is None:
        return _generic(code, name)
    category, ko, en, fixable, retryable = row
    return ActionResultInfo(int(code), name, category, ko, en, fixable, retryable)


def _generic(code: int, name: str | None = None) -> ActionResultInfo:
    return ActionResultInfo(
        code=int(code),
        name=name or f"ActionResult({int(code)})",
        category="other",
        reason_ko=f"게임이 주문을 거부했습니다 (코드 {int(code)})",
        reason_en=f"the game rejected the order (code {int(code)})",
        user_fixable=False,
        retryable=False,
        known=False,
    )


def describe_action_result(code: object) -> ActionResultInfo:
    """Describe any result value (int, protocol enum or python-sc2 ``ActionResult``).

    Unknown or future codes map to a safe generic "game rejected the order (code N)".
    ``Success`` is the only value reported as accepted.
    """

    raw = getattr(code, "value", code)
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise TypeError(f"ActionResult code must be an int, got {type(code).__name__}")
    name = action_result_name(raw)
    if name is None:
        return _generic(raw)
    return describe_action_result_name(name, raw)


def game_result_for(code: object) -> dict[str, object]:
    """``game_result`` mapping for one explicit result code."""

    return describe_action_result(code).to_game_result()


def unknown_game_result() -> dict[str, object]:
    """``game_result`` mapping for "no response seen yet" (``accepted`` is ``None``)."""

    return {
        "accepted": None,
        "code": None,
        "name": None,
        "category": None,
        "reason_ko": None,
        "reason_en": None,
        "user_fixable": None,
        "retryable": None,
    }


# --------------------------------------------------------------------------- tracking

DEFAULT_RESPONSE_WINDOW_LOOPS: Final[int] = 112
"""Game loops (about five seconds at 22.4 loops/s) an order may stay unresolved."""

_MAX_ORDERS: Final[int] = 1024


@dataclass
class OrderRecord:
    """One order handed to ``bot.do`` and what the game said about it."""

    seq: int
    frame: int
    unit_tag: int
    ability_ids: tuple[int, ...]
    code: int | None = None
    channel: str | None = None
    resolved_frame: int | None = None

    @property
    def accepted(self) -> bool | None:
        if self.code is None:
            return None
        return self.code == SUCCESS_CODE


@dataclass
class GameResultTracker:
    """Per-order ledger of ``RequestAction`` results and observation ``action_errors``.

    Orders are registered when the adapter queues them.  Results are attached either by the
    request channel (``record_response``: one result per submitted raw action, keyed by
    unit tag and ability id) or by the observation channel (``ingest_action_errors``).  A
    rejection is never overwritten by a later success; an error that follows a ``Success``
    response overrides it (the game's red text wins).  Nothing is ever promoted to
    ``accepted=True`` without an explicit ``Success`` code.
    """

    response_window_loops: int = DEFAULT_RESPONSE_WINDOW_LOOPS
    _orders: deque[OrderRecord] = field(default_factory=lambda: deque(maxlen=_MAX_ORDERS), repr=False)
    _seq: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def mark(self) -> int:
        """Sequence number after the last registered order (use with ``since``)."""

        return self._seq

    def register(self, unit_tag: int, ability_ids: Iterable[int], frame: int) -> OrderRecord:
        ids = tuple(dict.fromkeys(int(item) for item in ability_ids if int(item) > 0))
        with self._lock:
            self._seq += 1
            order = OrderRecord(self._seq, int(frame), int(unit_tag), ids)
            self._orders.append(order)
        return order

    def orders(self, *, since: int = 0, until: int | None = None) -> tuple[OrderRecord, ...]:
        with self._lock:
            return tuple(
                order
                for order in self._orders
                if order.seq > since and (until is None or order.seq <= until)
            )

    @staticmethod
    def _abilities_match(order: OrderRecord, ability_id: int) -> bool:
        return ability_id <= 0 or not order.ability_ids or ability_id in order.ability_ids

    def _apply(self, order: OrderRecord, code: int, channel: str, frame: int) -> None:
        if order.code is not None and order.code != SUCCESS_CODE:
            return  # first rejection stands
        if order.code is None or code != SUCCESS_CODE:
            order.code, order.channel, order.resolved_frame = int(code), channel, int(frame)

    def record_response(
        self,
        frame: int,
        results: Sequence[tuple[Sequence[int], int, int]],
    ) -> int:
        """Attach ``(unit_tags, ability_id, code)`` request results; returns orders resolved."""

        resolved = 0
        with self._lock:
            for unit_tags, ability_id, code in results:
                for tag in unit_tags:
                    order = self._latest_unresolved(int(tag), int(ability_id))
                    if order is not None:
                        self._apply(order, int(code), "request_response", frame)
                        resolved += 1
        return resolved

    def ingest_action_errors(
        self,
        frame: int,
        errors: Iterable[tuple[int, int, int]],
    ) -> int:
        """Attach observation ``action_errors`` ``(unit_tag, ability_id, result)``."""

        resolved = 0
        with self._lock:
            for unit_tag, ability_id, code in errors:
                if int(code) == SUCCESS_CODE:
                    continue
                order = self._latest_match(int(unit_tag), int(ability_id), int(frame))
                if order is not None:
                    before = order.code
                    self._apply(order, int(code), "observation_action_errors", frame)
                    resolved += before != order.code
        return resolved

    def _candidates(self, tag: int, ability_id: int) -> list[OrderRecord]:
        return [o for o in reversed(self._orders) if o.unit_tag == tag and self._abilities_match(o, ability_id)]

    def _latest_unresolved(self, tag: int, ability_id: int) -> OrderRecord | None:
        # Oldest unresolved first: results answer submissions in order.
        pending = [o for o in self._candidates(tag, ability_id) if o.code is None]
        return pending[-1] if pending else None

    def _latest_match(self, tag: int, ability_id: int, frame: int) -> OrderRecord | None:
        window = self.response_window_loops
        candidates = [o for o in self._candidates(tag, ability_id) if frame - o.frame <= window]
        for order in candidates:
            if order.code is None:
                return order
        return candidates[0] if candidates else None

    def summarize(
        self,
        *,
        since: int = 0,
        until: int | None = None,
        unit_tags: Iterable[int] | None = None,
        min_frame: int | None = None,
    ) -> dict[str, object] | None:
        """Aggregate ``game_result`` for the selected orders, or ``None`` when there are none."""

        tag_set = {int(tag) for tag in unit_tags} if unit_tags is not None else None
        selected = [
            order
            for order in self.orders(since=since, until=until)
            if (tag_set is None or order.unit_tag in tag_set)
            and (min_frame is None or order.frame >= min_frame)
        ]
        return summarize_orders(selected)


def summarize_orders(orders: Sequence[OrderRecord]) -> dict[str, object] | None:
    """Fold per-order outcomes: any rejection wins; ``accepted=True`` needs all explicit Success."""

    if not orders:
        return None
    rejected = [o for o in orders if o.code is not None and o.code != SUCCESS_CODE]
    accepted = [o for o in orders if o.code == SUCCESS_CODE]
    pending = [o for o in orders if o.code is None]
    if rejected:
        first = min(rejected, key=lambda o: o.seq)
        result = game_result_for(first.code)
        result["channel"] = first.channel
    elif not pending:
        result = game_result_for(SUCCESS_CODE)
        result["channel"] = accepted[-1].channel
    else:
        result = unknown_game_result()
        result["channel"] = None
    result["orders"] = len(orders)
    result["accepted_count"] = len(accepted)
    result["rejected_count"] = len(rejected)
    result["pending_count"] = len(pending)
    frames = [o.resolved_frame for o in orders if o.resolved_frame is not None]
    result["last_resolved_frame"] = max(frames) if frames else None
    return result


def is_rejected(game_result: object) -> bool:
    """True only for an explicit rejection (``accepted is False``)."""

    return isinstance(game_result, Mapping) and game_result.get("accepted") is False
