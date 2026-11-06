"""Offline SC2 raw-protocol harness: real python-sc2 ``Unit``/``BotAI``/``GameData`` over stableid.json.

No StarCraft II binary is involved.  ``build_game_data`` constructs a genuine
``sc2.game_data.GameData`` from a ``ResponseData`` protobuf populated from
``stableid.json`` (every Unit/Ability/Upgrade id and name) plus python-sc2's own
creation/research ability tables, so ``Unit.train/build/research`` resolve ability
ids exactly like they do in a live game.  Commands the product issues are
collected through the *real* ``BotAI.do`` and lowered with python-sc2's own
``combine_actions`` into ``s2clientprotocol`` ``ActionRaw`` messages, which are then
wrapped in a ``RequestAction`` and round-tripped through serialization.

This proves request SHAPE (ability id, unit tags, target kind, queue flag). It does
not prove that SC2 accepts the order in a given game state.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from s2clientprotocol import raw_pb2, sc2api_pb2
from sc2.action import combine_actions
from sc2.bot_ai import BotAI
from sc2.data import Attribute, Race
from sc2.dicts.unit_research_abilities import RESEARCH_INFO
from sc2.dicts.unit_train_build_abilities import TRAIN_INFO
from sc2.game_data import GameData
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sc2.unit import Unit
from sc2.units import Units

from scripts.sc2_accuracy import audit

ENUMS = audit.load_header_enums()
ABILITY_TARGET_KIND = {int(num): target for num, (_, target) in ENUMS["abilities"].items()}
"""s2client-api (C++) ability id -> declared target kind ("None", "Point", "Unit", ...)."""


def _remap_kinds() -> dict[int, str]:
    """General (remapped) ids such as 3793 HoldPosition inherit the kind of the id that remaps to them."""

    stable = audit.load_stableid() or {"Abilities": []}
    out: dict[int, str] = {}
    for ability in stable["Abilities"]:
        remap = ability.get("remapid")
        kind = ABILITY_TARGET_KIND.get(ability["id"])
        if remap and kind and remap not in ABILITY_TARGET_KIND:
            out.setdefault(remap, kind)
    return out


ABILITY_TARGET_KIND = {**_remap_kinds(), **ABILITY_TARGET_KIND}


def stableid_available() -> bool:
    return audit.stableid_path().is_file()


def _structure_type_ids() -> set[int]:
    built = {u.value for m in TRAIN_INFO.values() for u in m if u in TRAIN_INFO.get(UnitTypeId.SCV, {})}
    addons = {
        UnitTypeId.BARRACKSTECHLAB, UnitTypeId.BARRACKSREACTOR, UnitTypeId.FACTORYTECHLAB,
        UnitTypeId.FACTORYREACTOR, UnitTypeId.STARPORTTECHLAB, UnitTypeId.STARPORTREACTOR,
        UnitTypeId.ORBITALCOMMAND, UnitTypeId.PLANETARYFORTRESS,
    }
    return built | {u.value for u in addons}


BIOLOGICAL_TERRAN = {
    UnitTypeId.MARINE.value, UnitTypeId.MARAUDER.value, UnitTypeId.REAPER.value,
    UnitTypeId.GHOST.value, UnitTypeId.SCV.value,
    # Non-Terran bio the targeting contracts need (public game data; stableid.json has no attributes).
    UnitTypeId.ZEALOT.value, UnitTypeId.ZERGLING.value,
}

MECHANICAL_UNITS = {
    UnitTypeId.SIEGETANK.value, UnitTypeId.SIEGETANKSIEGED.value, UnitTypeId.THOR.value, UnitTypeId.THORAP.value,
    UnitTypeId.HELLION.value, UnitTypeId.CYCLONE.value, UnitTypeId.WIDOWMINE.value,
    UnitTypeId.WIDOWMINEBURROWED.value, UnitTypeId.MEDIVAC.value, UnitTypeId.VIKINGFIGHTER.value,
    UnitTypeId.VIKINGASSAULT.value, UnitTypeId.LIBERATOR.value, UnitTypeId.LIBERATORAG.value,
    UnitTypeId.BANSHEE.value, UnitTypeId.RAVEN.value, UnitTypeId.BATTLECRUISER.value,
    UnitTypeId.MULE.value, UnitTypeId.IMMORTAL.value,
}

# Supply (food_required) per unit, public game data; stableid.json has none.  Needed for supply-gating contracts.
FOOD_REQUIRED = {
    UnitTypeId.SCV.value: 1.0, UnitTypeId.MARINE.value: 1.0, UnitTypeId.MARAUDER.value: 2.0,
    UnitTypeId.REAPER.value: 1.0, UnitTypeId.GHOST.value: 2.0, UnitTypeId.HELLION.value: 2.0,
    UnitTypeId.HELLIONTANK.value: 2.0, UnitTypeId.SIEGETANK.value: 3.0, UnitTypeId.CYCLONE.value: 3.0,
    UnitTypeId.THOR.value: 6.0, UnitTypeId.WIDOWMINE.value: 2.0, UnitTypeId.MEDIVAC.value: 2.0,
    UnitTypeId.VIKINGFIGHTER.value: 2.0, UnitTypeId.LIBERATOR.value: 3.0, UnitTypeId.BANSHEE.value: 3.0,
    UnitTypeId.RAVEN.value: 2.0, UnitTypeId.BATTLECRUISER.value: 6.0,
}

# (minerals, vespene) per unit/structure, public game data; stableid.json has none.  Needed for resource-gating contracts.
# cargo_size per unit (public game data, UnitTypeData.cargo_size in the docs); stableid.json has none.
CARGO_SIZE = {
    UnitTypeId.SCV: 1, UnitTypeId.MARINE: 1, UnitTypeId.MARAUDER: 2, UnitTypeId.REAPER: 1, UnitTypeId.GHOST: 2,
    UnitTypeId.HELLION: 2, UnitTypeId.HELLIONTANK: 2, UnitTypeId.WIDOWMINE: 2, UnitTypeId.CYCLONE: 4,
    UnitTypeId.SIEGETANK: 4, UnitTypeId.THOR: 8,
}
MEDIVAC_CARGO_MAX = 8

COSTS = {
    UnitTypeId.SCV: (50, 0), UnitTypeId.MARINE: (50, 0), UnitTypeId.MARAUDER: (100, 25), UnitTypeId.GHOST: (150, 125),
    UnitTypeId.SIEGETANK: (150, 125), UnitTypeId.MEDIVAC: (100, 100), UnitTypeId.BATTLECRUISER: (400, 300),
    UnitTypeId.SUPPLYDEPOT: (100, 0), UnitTypeId.BARRACKS: (150, 0), UnitTypeId.FACTORY: (150, 100),
    UnitTypeId.STARPORT: (150, 100), UnitTypeId.COMMANDCENTER: (400, 0), UnitTypeId.REFINERY: (75, 0),
    UnitTypeId.ENGINEERINGBAY: (125, 0),
}


def build_game_data() -> GameData:
    stable = audit.load_stableid()
    if stable is None:
        raise FileNotFoundError(f"stableid.json not found at {audit.stableid_path()}")
    response = sc2api_pb2.ResponseData()
    for ability in stable["Abilities"]:
        proto = response.abilities.add()
        proto.ability_id = ability["id"]
        proto.link_name = ability["name"]
        proto.button_name = ability.get("buttonname", "")
        proto.friendly_name = ability.get("friendlyname", "")
        if "remapid" in ability:
            proto.remaps_to_ability_id = ability["remapid"]
        kind = ABILITY_TARGET_KIND.get(ability["id"])
        code = {"None": 1, "Point": 2, "Unit": 3, "Unit, Point": 4, "Point, Unit": 4}.get(kind or "")
        # data_pb2.AbilityData.Target.  Ids the 2019 header does not list (remapped general ids
        # such as 3795 Patrol, and post-2019 abilities) are declared PointOrUnit so python-sc2's own
        # target-kind warning cannot fire spuriously; ``target_shape_ok`` skips them as unverified.
        proto.target = code or 4
    created: dict[int, int] = {}
    for producer_table in TRAIN_INFO.values():
        for unit, info in producer_table.items():
            created[unit.value] = info["ability"].value
    # python-sc2's TRAIN_INFO has no add-on rows; the live game data does (s2client-api header:
    # BUILD_TECHLAB_* / BUILD_REACTOR_* are the creation abilities, Target: None).
    for addon, ability in (
        (UnitTypeId.BARRACKSTECHLAB, AbilityId.BUILD_TECHLAB_BARRACKS),
        (UnitTypeId.BARRACKSREACTOR, AbilityId.BUILD_REACTOR_BARRACKS),
        (UnitTypeId.FACTORYTECHLAB, AbilityId.BUILD_TECHLAB_FACTORY),
        (UnitTypeId.FACTORYREACTOR, AbilityId.BUILD_REACTOR_FACTORY),
        (UnitTypeId.STARPORTTECHLAB, AbilityId.BUILD_TECHLAB_STARPORT),
        (UnitTypeId.STARPORTREACTOR, AbilityId.BUILD_REACTOR_STARPORT),
    ):
        created[addon.value] = ability.value
    structures = _structure_type_ids()
    for unit in stable["Units"]:
        proto = response.units.add()
        proto.unit_id = unit["id"]
        proto.name = unit["name"]
        proto.available = True
        proto.race = Race.Terran.value
        if unit["id"] in created:
            proto.ability_id = created[unit["id"]]
        if unit["id"] in structures:
            proto.attributes.append(Attribute.Structure.value)
        if unit["id"] in BIOLOGICAL_TERRAN:
            # stableid.json carries no unit attributes; the harness supplies the (stable, public) bio set.
            proto.attributes.append(Attribute.Biological.value)
        if unit["id"] in MECHANICAL_UNITS:
            proto.attributes.append(Attribute.Mechanical.value)
        if unit["id"] in FOOD_REQUIRED:
            proto.food_required = FOOD_REQUIRED[unit["id"]]
        proto.cargo_size = next((c for u, c in CARGO_SIZE.items() if u.value == unit["id"]), 0)
        cost = next((c for u, c in COSTS.items() if u.value == unit["id"]), None)
        if cost is not None:
            proto.mineral_cost, proto.vespene_cost = cost
    researched: dict[int, int] = {}
    for research_table in RESEARCH_INFO.values():
        for upgrade, info in research_table.items():
            researched[upgrade.value] = info["ability"].value
    for upgrade in stable["Upgrades"]:
        proto = response.upgrades.add()
        proto.upgrade_id = upgrade["id"]
        proto.name = upgrade["name"]
        if upgrade["id"] in researched:
            proto.ability_id = researched[upgrade["id"]]
    return GameData(response)


class HarnessBot(BotAI):
    """A real ``BotAI`` whose world queries are permissive and whose units are protos."""

    def __init__(self, game_data: GameData) -> None:
        super().__init__()
        self.game_data = game_data
        self.race = Race.Terran
        self.state = _State()
        self._expansion_positions_list = [Point2((20.0, 20.0)), Point2((50.0, 50.0))]
        self.minerals = 100000
        self.vespene = 100000
        self.supply_left = 200
        self.supply_cap = 200
        self._next_tag = 0x100000001

    # -- world queries are permissive: the harness validates request shape, not map logic
    def in_map_bounds(self, pos) -> bool:  # type: ignore[override]
        return True

    def in_placement_grid(self, pos) -> bool:  # type: ignore[override]
        return True

    def in_pathing_grid(self, pos) -> bool:  # type: ignore[override]
        return True

    def is_visible(self, pos) -> bool:  # type: ignore[override]
        return True

    async def can_place_single(self, building, position) -> bool:  # type: ignore[override]
        return True

    async def can_place(self, building, positions):  # type: ignore[override]
        return [True for _ in positions]

    # -- unit factory
    def add_unit(self, unit_type: UnitTypeId, *, x: float = 20.0, y: float = 20.0, structure: bool = False,
                 orders: tuple[AbilityId, ...] = (), health: float = 100.0, health_max: float = 100.0,
                 enemy: bool = False, add_on_tag: int = 0, energy: float | None = None,
                 energy_max: float | None = None, build_progress: float = 1.0, weapon_cooldown: float = 0.0,
                 buff_ids: tuple[int, ...] = (), cargo_taken: int = 0) -> Unit:
        self._next_tag += 1
        proto = raw_pb2.Unit(
            tag=self._next_tag, unit_type=unit_type.value, alliance=4 if enemy else 1,
            build_progress=build_progress, health=health, health_max=health_max, add_on_tag=add_on_tag,
            weapon_cooldown=weapon_cooldown,
        )
        if energy is not None:
            proto.energy = energy
            proto.energy_max = energy_max if energy_max is not None else 200.0
        proto.buff_ids.extend(buff_ids)
        if unit_type == UnitTypeId.MEDIVAC:
            proto.cargo_space_max = MEDIVAC_CARGO_MAX
            proto.cargo_space_taken = cargo_taken
        if unit_type in {UnitTypeId.BARRACKSTECHLAB, UnitTypeId.FACTORYTECHLAB, UnitTypeId.STARPORTTECHLAB}:
            self.techlab_tags.add(self._next_tag)
        if unit_type in {UnitTypeId.BARRACKSREACTOR, UnitTypeId.FACTORYREACTOR, UnitTypeId.STARPORTREACTOR}:
            self.reactor_tags.add(self._next_tag)
        proto.pos.x, proto.pos.y = x, y
        for ability in orders:
            proto.orders.add().ability_id = ability.value
        unit = Unit(proto, self)
        if enemy:
            (self.enemy_structures if unit.is_structure else self.enemy_units).append(unit)
            return unit
        group = self.structures if (structure or unit.is_structure) else self.units
        group.append(unit)
        if unit_type == UnitTypeId.SCV:
            self.workers.append(unit)
        self.all_own_units.append(unit)
        return unit

    # -- request extraction
    def drain_actions(self) -> list[Any]:
        """Lower and clear everything issued through ``BotAI.do``; returns ``ActionRaw`` messages."""

        commands = list(self.actions)
        self.actions.clear()
        return list(combine_actions(commands))


class _State:
    game_loop = 1
    upgrades: set = set()


def make_adapter(bot: HarnessBot):
    """The product's real ``PythonSC2BotAdapter`` over the harness bot with fixed semantic map points."""

    from starcraft_commander.map_resolver import MapPoint, SC2MapResolver
    from starcraft_commander.python_sc2_adapter import PythonSC2BotAdapter

    return PythonSC2BotAdapter(
        bot=bot,
        map_resolver=SC2MapResolver(
            positions={
                "self_main": MapPoint(20.0, 20.0),
                "self_ramp": MapPoint(26.0, 24.0),
                "self_natural": MapPoint(50.0, 50.0),
                "self_third": MapPoint(80.0, 40.0),
                "self_mineral_line": MapPoint(18.0, 22.0),
                "self_geyser": MapPoint(24.0, 16.0),
                "enemy_main": MapPoint(180.0, 180.0),
                "enemy_natural": MapPoint(150.0, 150.0),
                "enemy_mineral_line": MapPoint(176.0, 184.0),
            }
        ),
    )


def run_plan(bot: HarnessBot, *actions, adapter=None):
    """Execute Direct actions through the real ``SC2RuntimeExecutor`` and return ``(result, views, warnings)``.

    ``views`` are the flattened raw ``ActionRawUnitCommand`` requests that left ``BotAI.do``
    (after python-sc2's own ``combine_actions``), each verified to survive a ``RequestAction``
    serialize/parse round trip.  ``warnings`` are python-sc2 ``Unit.__call__`` target-kind warnings.
    """

    import asyncio
    import warnings as _warnings

    from starcraft_commander.contracts import SC2ExecutionPlan
    from starcraft_commander.sc2_executor import SC2RuntimeExecutor

    adapter = adapter or make_adapter(bot)
    executor = SC2RuntimeExecutor(bot=adapter)
    plan = SC2ExecutionPlan(intent_name="sc2_accuracy", ordered_actions=actions)
    with _warnings.catch_warnings(record=True) as caught:
        _warnings.simplefilter("always")
        result = asyncio.run(executor.execute(plan))
    raws = bot.drain_actions()
    request_action(raws)  # must serialize + parse
    views = [command_view(raw) for raw in raws]
    messages = [str(w.message) for w in caught if issubclass(w.category, RuntimeWarning)]
    return result, views, messages


def action_reports(result) -> list[dict[str, Any]]:
    reports = result.audit.get("action_reports", {})
    return [reports[key] for key in sorted(reports, key=int)]


def make_bot(*units: tuple[UnitTypeId, int]) -> HarnessBot:
    """Bot with ``count`` units of each type; structures are detected from game data."""

    bot = HarnessBot(build_game_data())
    for index, (unit_type, count) in enumerate(units):
        for offset in range(count):
            bot.add_unit(unit_type, x=20.0 + index + offset * 0.5, y=20.0 + index)
    return bot


def request_action(action_raws: list[Any]) -> sc2api_pb2.Request:
    """Wrap ``ActionRaw`` messages in a ``RequestAction`` (what ``Client.actions`` sends) and round-trip it."""

    request = sc2api_pb2.Request()
    for raw in action_raws:
        request.action.actions.add().action_raw.CopyFrom(raw)
    return sc2api_pb2.Request.FromString(request.SerializeToString())


def command_view(action_raw: Any) -> dict[str, Any]:
    """Flat, assertion-friendly view of one ``ActionRawUnitCommand``."""

    command = action_raw.unit_command
    kind = command.WhichOneof("target")
    return {
        "ability_id": command.ability_id,
        "ability_name": AbilityId(command.ability_id).name,
        "unit_tags": sorted(command.unit_tags),
        "target": kind,  # None | "target_world_space_pos" | "target_unit_tag"
        "pos": (command.target_world_space_pos.x, command.target_world_space_pos.y) if kind == "target_world_space_pos" else None,
        "unit_tag": command.target_unit_tag if kind == "target_unit_tag" else None,
        "queue": command.queue_command,
        "declared_target_kind": ABILITY_TARGET_KIND.get(command.ability_id),
    }


def target_shape_ok(view: dict[str, Any]) -> bool:
    """Does the request's target field agree with the ability's declared target kind (s2client-api header)?"""

    declared = view["declared_target_kind"]
    if declared is None:  # id newer than the 2019 header: cannot judge
        return True
    allowed = {
        "None": {None},
        "Point": {"target_world_space_pos"},
        "Unit": {"target_unit_tag"},
        "Unit, Point": {"target_world_space_pos", "target_unit_tag"},
        "Point, Unit": {"target_world_space_pos", "target_unit_tag"},
    }.get(declared)
    return True if allowed is None else view["target"] in allowed


_SAFE = re.compile(r"[^A-Za-z0-9]+")


def env_stableid_hint() -> str:
    return os.environ.get("VOI_SC2_STABLEID", "") or str(Path(audit.DEFAULT_STABLEID))
