"""Real python-sc2 BotAI adapter for semantic SC2 command actions.

This is the handoff Step 2 bridge between planned semantic commands and a
live python-sc2 ``BotAI`` runtime. ``SC2RuntimeExecutor.execute`` dispatches
every planned :class:`SC2CommandAction` by calling the method named after its
``action_type`` on the bound runtime adapter, so :class:`PythonSC2BotAdapter`
implements exactly those seven method names and translates them into
duck-typed BotAI operations: worker gather, build, train, move, attack-move,
repair, and state observation. Bot objects are never isinstance-checked
against python-sc2 types and python-sc2 itself is only lazy-imported inside
functions, so this module stays importable without StarCraft II, python-sc2,
faster-whisper, or sounddevice installed.

Real game integration must issue these calls inside the python-sc2 game loop
(for example from ``BotAI.on_step`` in the live pipeline / ``demo_sc2`` demo
planned by ``docs/claude-handoff.md`` Step 5). The adapter deliberately
defines none of the lifecycle method names probed by ``SC2RuntimeExecutor``
(``start``, ``close``, ``stop``, ``on_start``, ``on_end``) so executor
lifecycle hooks can never collide with python-sc2 ``BotAI`` lifecycle
semantics.

Counted semantic methods return a structured
:class:`~starcraft_commander.contracts.SC2ActionReport` carrying the
requested versus actually issued order counts, so partial issuance (fewer
units available than the commander asked for) is never collapsed into an
unqualified boolean success. ``build_structure`` returns a plain bool
(one whole structure either starts or it does not), and ``observe`` returns
a JSON-ready mapping that the executor stores under
``result.audit['observations']``. Attribute gaps on the bot are checked
before use; genuine runtime exceptions propagate to the executor, which
captures them as structured ``SC2ExecutionError`` entries with action
context.
"""

from __future__ import annotations

import inspect
import math
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Protocol, runtime_checkable

from starcraft_commander.contracts import SC2ActionReport, SC2CommandAction
from starcraft_commander.map_resolver import (
    MapPoint,
    SC2MapResolver,
    SC2MapResolverInterface,
)
from starcraft_commander.sc2_executor import SC2_UNIT_TYPE_IDS
from starcraft_commander.state_resolver import (
    DEFAULT_SC2_STATE_RESOLVER,
    SC2_WORKER_TYPE_NAME,
    SC2StateResolverInterface,
)


SC2_ADAPTER_ACTION_METHOD_NAMES: Final[tuple[str, ...]] = (
    "assign_workers",
    "build_structure",
    "train_unit",
    "move_group",
    "attack_move",
    "repair",
    "observe",
)
"""The seven semantic action methods ``SC2RuntimeExecutor`` dispatches to."""

SC2_EXECUTOR_LIFECYCLE_METHOD_NAMES: Final[frozenset[str]] = frozenset(
    {"start", "close", "stop", "on_start", "on_end"}
)
"""Lifecycle hook names probed by ``SC2RuntimeExecutor`` with zero arguments.

The adapter must never define these: python-sc2 ``BotAI`` lifecycle methods
share some of these names with different signatures and game-loop semantics.
"""

SC2_MINERAL_RESOURCE_NAMES: Final[frozenset[str]] = frozenset({"mineral", "minerals"})
"""Gather-action target values routed to the nearest mineral field."""

SC2_GAS_RESOURCE_NAMES: Final[frozenset[str]] = frozenset({"gas", "vespene"})
"""Gather-action target values routed to a completed own refinery.

A bare vespene geyser is never a gather target: the real game silently
rejects HARVEST_GATHER on a geyser without a completed extraction building,
so the adapter refuses honestly instead of issuing a dead order.
"""

SC2_GAS_STRUCTURE_TYPE_NAMES: Final[frozenset[str]] = frozenset(
    {"REFINERY", "EXTRACTOR", "ASSIMILATOR"}
)
"""Normalized gas-extraction structure type names requiring a geyser Unit.

Real python-sc2 requires the build target for these structures to be the
vespene geyser *unit* (``Unit.build_gas``), never a map position.
"""

SC2_GENERIC_REPAIR_TARGET_NAMES: Final[frozenset[str]] = frozenset(
    {
        "ANY",
        "ANYBUILDING",
        "ANYSTRUCTURE",
        "BASE",
        "BUILDING",
        "BUILDINGS",
        "DAMAGED",
        "STRUCTURE",
        "STRUCTURES",
    }
)
"""Normalized repair targets that accept any damaged own structure."""

SC2_COMMAND_CENTER_SOURCE_STRUCTURE: Final[str] = "Command Center"
"""Planner metadata marker that lets ``build_structure`` prefer expansion."""

PYTHON_SC2_UNIT_TYPE_HINT: Final[str] = (
    "python-sc2 (importable package 'sc2') is required to resolve UnitTypeId "
    "names. Install it with: pip install 'voistarcraft[sc2]' (or: pip install "
    "burnysc2), or inject PythonSC2BotAdapter(unit_type_resolver=...) for "
    "offline tests. python-sc2('sc2' 패키지)가 설치되어 있지 않아 UnitTypeId "
    "이름을 해석할 수 없습니다. pip install 'voistarcraft[sc2]' 또는 "
    "pip install burnysc2 명령으로 설치하거나, 오프라인 테스트에서는 "
    "unit_type_resolver를 주입하세요."
)
"""Actionable bilingual guidance raised when UnitTypeId lookup needs sc2."""

_KNOWN_UNIT_TYPE_NAMES: Final[frozenset[str]] = frozenset(SC2_UNIT_TYPE_IDS.values())
"""Normalized unit type names the planner can emit as group subjects."""

_WORKER_KEYWORD: Final[str] = "WORKER"
"""Free-text marker selecting worker units (covers '1 SCV', 'worker_scout')."""

_UNSET: Final[object] = object()
"""Internal sentinel distinguishing missing attributes from ``None`` values."""

_LEADING_COUNT_PATTERN: Final[re.Pattern[str]] = re.compile(r"\s*(\d+)")
"""Leading integer parser for free-text unit groups such as '2 Marines'."""


@runtime_checkable
class SC2BotAdapterInterface(Protocol):
    """Semantic action seam between ``SC2RuntimeExecutor`` and a BotAI bridge."""

    async def assign_workers(self, action: SC2CommandAction) -> SC2ActionReport:
        """Send workers to gather the requested resource near the main base."""

    async def build_structure(self, action: SC2CommandAction) -> bool:
        """Place one structure near the resolved semantic map target."""

    async def train_unit(self, action: SC2CommandAction) -> SC2ActionReport:
        """Queue unit training on ready idle producers of the planned type."""

    async def move_group(self, action: SC2CommandAction) -> SC2ActionReport:
        """Move the selected unit group to the resolved semantic map target."""

    async def attack_move(self, action: SC2CommandAction) -> SC2ActionReport:
        """Attack-move the selected unit group to the resolved map target."""

    async def repair(self, action: SC2CommandAction) -> SC2ActionReport:
        """Send workers to repair the first damaged matching own entity."""

    async def observe(self, action: SC2CommandAction) -> Mapping[str, object]:
        """Return a JSON-ready commander state snapshot observation."""


@dataclass
class PythonSC2BotAdapter:
    """Duck-typed bridge from semantic SC2 actions to python-sc2 BotAI calls.

    The adapter is intentionally **not** frozen: ``map_resolver`` is built
    lazily from the bot on first use (python-sc2 map data is only complete
    once the game has started). Every method checks bot capabilities through
    ``getattr`` before calling them and refuses (``False``) instead of
    guessing; unexpected runtime exceptions propagate to the executor, which
    records them as structured errors with action context.
    """

    bot: object
    map_resolver: SC2MapResolver | None = None
    state_resolver: SC2StateResolverInterface = DEFAULT_SC2_STATE_RESOLVER
    unit_type_resolver: Callable[[str], object] | None = None

    def __post_init__(self) -> None:
        if self.bot is None:
            raise ValueError("PythonSC2BotAdapter bot must not be None.")
        if self.map_resolver is not None and not callable(
            getattr(self.map_resolver, "resolve_point", None)
        ):
            raise TypeError(
                "PythonSC2BotAdapter map_resolver must implement resolve_point()."
            )
        if not callable(getattr(self.state_resolver, "resolve", None)):
            raise TypeError(
                "PythonSC2BotAdapter state_resolver must implement resolve()."
            )
        if self.unit_type_resolver is not None and not callable(self.unit_type_resolver):
            raise TypeError(
                "PythonSC2BotAdapter unit_type_resolver must be callable or None."
            )

    async def assign_workers(self, action: SC2CommandAction) -> SC2ActionReport:
        """Gather up to ``action.count`` workers onto the requested resource.

        Idle workers (``bot.workers.idle``) are preferred, falling back to all
        of ``bot.workers``. The gather target is the mineral field nearest
        ``bot.start_location`` for minerals, or the nearest completed own
        refinery for gas (a bare geyser is never targeted: the live game
        silently rejects gathering from it). The returned report carries the
        requested versus issued counts so fewer-than-requested workers are
        surfaced as a partial application, never as unqualified success.
        """

        if action.count <= 0:
            return _refusal_report(action.count, "non_positive_count")
        target_unit = self._resource_target(action.target)
        if target_unit is None:
            return _refusal_report(action.count, "no_gather_target")
        issued = 0
        for worker in self._worker_pool():
            if issued >= action.count:
                break
            gather = getattr(worker, "gather", None)
            if not callable(gather):
                continue
            if await self._issue_unit_order(gather, target_unit):
                issued += 1
        return _issuance_report(action.count, issued, "insufficient_workers")

    async def build_structure(self, action: SC2CommandAction) -> bool:
        """Build ``action.subject`` near the resolved semantic map target.

        ``action.subject`` is a python-sc2 ``UnitTypeId`` name string such as
        ``SUPPLYDEPOT``. Explicitly unaffordable builds are refused. EXPAND
        plans (planner metadata ``source_structure == 'Command Center'``)
        prefer ``await bot.expand_now()`` when the bot provides it. Gas
        structures (Refinery) require a geyser *unit* in real python-sc2, so
        they are built through ``worker.build_gas`` (or ``bot.build`` with the
        geyser unit) on the nearest free geyser, refusing honestly when no
        free geyser or worker exists.
        """

        type_id = self._resolve_unit_type(action.subject)
        if not await self._is_affordable(type_id):
            return False
        source_structure = str(action.metadata.get("source_structure", ""))
        if source_structure == SC2_COMMAND_CENTER_SOURCE_STRUCTURE:
            expand_now = getattr(self.bot, "expand_now", None)
            if callable(expand_now):
                return await _call_bot_operation(expand_now)
        if _normalized_name(action.subject) in SC2_GAS_STRUCTURE_TYPE_NAMES:
            return await self._build_gas_structure(action, type_id)
        position = self._resolve_target_point(action.target)
        if position is None:
            return False
        build = getattr(self.bot, "build", None)
        if not callable(build):
            return False
        return await _call_bot_operation(build, type_id, near=_game_point(position))

    async def _build_gas_structure(
        self,
        action: SC2CommandAction,
        type_id: object,
    ) -> bool:
        """Build one gas structure on the nearest free vespene geyser unit.

        Real python-sc2 rejects gas builds targeted at a position: the build
        target must be the geyser ``Unit``. ``worker.build_gas(geyser)`` is
        preferred; when workers lack ``build_gas`` (offline fakes), the
        geyser unit is passed to ``bot.build`` instead, which real burnysc2
        also accepts for gas structures.
        """

        anchor = self._resolve_target_point(action.target)
        if anchor is None:
            anchor = _entity_point(getattr(self.bot, "start_location", None))
        geyser = self._free_geyser(anchor)
        if geyser is None:
            return False
        for worker in self._worker_pool():
            build_gas = getattr(worker, "build_gas", None)
            if callable(build_gas):
                return await self._issue_unit_order(build_gas, geyser)
        build = getattr(self.bot, "build", None)
        if not callable(build):
            return False
        return await _call_bot_operation(build, type_id, near=geyser)

    def _free_geyser(self, anchor: MapPoint | None) -> object | None:
        """Find the geyser unit nearest the anchor without an own gas building."""

        gas_structures = [
            structure
            for structure in _materialize(getattr(self.bot, "structures", None))
            if _entity_type_name(structure) in SC2_GAS_STRUCTURE_TYPE_NAMES
        ]
        taken_points = [
            point
            for structure in gas_structures
            if (point := _entity_point(structure)) is not None
        ]
        free = [
            geyser
            for geyser in _materialize(getattr(self.bot, "vespene_geyser", None))
            if not _point_is_taken(_entity_point(geyser), taken_points)
        ]
        return _nearest_entity(free, anchor)

    async def train_unit(self, action: SC2CommandAction) -> SC2ActionReport:
        """Queue up to ``action.count`` training orders on idle producers.

        Producers are ready idle own structures whose normalized type name
        matches ``action.metadata['producer']`` (for example ``BARRACKS``).
        Orders are distributed one per producer per pass. The returned report
        carries requested versus issued counts so a mid-batch stop (budget
        ran out) surfaces as a partial application, never as full success.
        """

        if action.count <= 0:
            return _refusal_report(action.count, "non_positive_count")
        producer_name = _normalized_name(action.metadata.get("producer"))
        if producer_name is None:
            return _refusal_report(action.count, "missing_producer_metadata")
        type_id = self._resolve_unit_type(action.subject)
        producers = [
            structure
            for structure in self._ready_idle_structures()
            if _entity_type_name(structure) == producer_name
        ]
        if not producers:
            return _refusal_report(action.count, "no_ready_idle_producer")
        issued = 0
        detail = "producers_stalled"
        while issued < action.count:
            issued_in_pass = 0
            for producer in producers:
                if issued >= action.count:
                    break
                if not await self._is_affordable(type_id):
                    return _issuance_report(action.count, issued, "unaffordable")
                train = getattr(producer, "train", None)
                if not callable(train):
                    continue
                if await self._issue_unit_order(train, type_id):
                    issued += 1
                    issued_in_pass += 1
            if issued_in_pass == 0:
                break
        return _issuance_report(action.count, issued, detail)

    async def move_group(self, action: SC2CommandAction) -> SC2ActionReport:
        """Move the unit group selected by ``action.subject`` to the target."""

        return await self._order_group(action, "move")

    async def attack_move(self, action: SC2CommandAction) -> SC2ActionReport:
        """Attack-move the selected unit group to the resolved map target."""

        return await self._order_group(action, "attack")

    async def repair(self, action: SC2CommandAction) -> SC2ActionReport:
        """Send up to ``action.count`` workers to repair the matched target.

        ``action.target`` is an entity name (for example ``front bunker``)
        matched loosely against own damaged structures first, then own
        damaged units; generic targets such as ``building`` accept any
        damaged own structure. Refuses when nothing damaged matches or no
        worker issued a repair order; fewer repairing workers than requested
        surface as a partial application in the returned report.
        """

        if action.count <= 0:
            return _refusal_report(action.count, "non_positive_count")
        target_unit = self._find_damaged_repair_target(action.target)
        if target_unit is None:
            return _refusal_report(action.count, "no_damaged_repair_target")
        issued = 0
        for worker in self._worker_pool():
            if issued >= action.count:
                break
            repair_order = getattr(worker, "repair", None)
            if not callable(repair_order):
                continue
            if await self._issue_unit_order(repair_order, target_unit):
                issued += 1
        return _issuance_report(action.count, issued, "insufficient_workers")

    async def observe(self, action: SC2CommandAction) -> Mapping[str, object]:
        """Resolve and return the commander state snapshot as a mapping.

        The executor stores any returned mapping under
        ``result.audit['observations'][str(action_index)]`` and counts the
        action as applied.
        """

        return self.state_resolver.resolve(self.bot).to_dict()

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready description of the adapter configuration."""

        return {
            "runtime_adapter": type(self.bot).__name__,
            "map_resolver_ready": self.map_resolver is not None,
            "state_resolver": type(self.state_resolver).__name__,
            "unit_type_resolver_injected": self.unit_type_resolver is not None,
            "action_methods": list(SC2_ADAPTER_ACTION_METHOD_NAMES),
        }

    def _resolve_map_resolver(self) -> SC2MapResolverInterface:
        """Return the bound map resolver, deriving it from the bot once."""

        if self.map_resolver is None:
            self.map_resolver = SC2MapResolver.from_bot(self.bot)
        return self.map_resolver

    def _resolve_target_point(self, target_name: str) -> MapPoint | None:
        """Resolve one semantic (or aliased) map target into a point."""

        return self._resolve_map_resolver().resolve_point(target_name)

    def _resolve_unit_type(self, type_name: str) -> object:
        """Resolve a ``UnitTypeId`` name through injection, bot, or python-sc2.

        Resolution order: the injected ``unit_type_resolver`` field, then a
        duck-typed ``bot.unit_type_id_resolver`` callable, then the real
        python-sc2 ``UnitTypeId`` enum (lazy import). Raises
        :class:`MissingPythonSC2Error` with actionable guidance when no
        resolver is available and python-sc2 is not installed.
        """

        if self.unit_type_resolver is not None:
            return self.unit_type_resolver(type_name)
        bot_resolver = getattr(self.bot, "unit_type_id_resolver", None)
        if callable(bot_resolver):
            return bot_resolver(type_name)
        try:
            from sc2.ids.unit_typeid import UnitTypeId
        except ImportError as error:
            raise MissingPythonSC2Error(PYTHON_SC2_UNIT_TYPE_HINT) from error
        try:
            return UnitTypeId[type_name]
        except KeyError as error:
            raise ValueError(
                f"Unknown python-sc2 UnitTypeId name: {type_name!r}."
            ) from error

    async def _is_affordable(self, type_id: object) -> bool:
        """Check ``bot.can_afford`` when present; refuse only explicit ``no``."""

        can_afford = getattr(self.bot, "can_afford", None)
        if not callable(can_afford):
            return True
        result = can_afford(type_id)
        if inspect.isawaitable(result):
            result = await result
        if result is None:
            return True
        return bool(result)

    async def _issue_unit_order(
        self,
        order_method: Callable[..., object],
        *args: object,
    ) -> bool:
        """Issue one unit order, preferring ``bot.do`` collection when present."""

        command = order_method(*args)
        if inspect.isawaitable(command):
            command = await command
        if command is None:
            return True
        if not command:
            return False
        do = getattr(self.bot, "do", None)
        if callable(do):
            outcome = do(command)
            if inspect.isawaitable(outcome):
                outcome = await outcome
            return outcome is None or bool(outcome)
        return True

    async def _order_group(
        self,
        action: SC2CommandAction,
        order_name: str,
    ) -> SC2ActionReport:
        """Issue one move/attack order per selected unit toward the target.

        The requested count is the leading integer of the subject (``6
        Marines`` requests six); when fewer matching units exist the report
        surfaces the shortfall as a partial application.
        """

        requested = _leading_count(action.subject)
        position = self._resolve_target_point(action.target)
        if position is None:
            return _refusal_report(requested, "unresolvable_target")
        destination = _game_point(position)
        issued = 0
        for unit in self._select_group(action.subject):
            order_method = getattr(unit, order_name, None)
            if not callable(order_method):
                continue
            if await self._issue_unit_order(order_method, destination):
                issued += 1
        return _issuance_report(requested, issued, "insufficient_units")

    def _select_group(self, subject: str) -> list[object]:
        """Select own units for a group order from a subject or free text.

        A normalized exact unit-type match wins. A known unit type name with
        zero matching own units selects nothing (honest refusal). Counted or
        pluralized type phrases (``6 Marines``, ``1 Marine``, ``Marines``)
        select only units of that named type — never substitutes — capped at
        the leading count; zero matching units is again an honest refusal.
        Remaining free text: worker phrases (``1 SCV``, ``worker_scout``)
        select workers (capped at the leading count, default one scout) and
        genuinely generic combat phrases (``available combat units``) select
        all non-worker army units capped at the leading count when present.
        """

        units = _materialize(getattr(self.bot, "units", None))
        normalized_subject = _normalized_name(subject)
        if normalized_subject is None:
            return []
        typed = [
            unit for unit in units if _entity_type_name(unit) == normalized_subject
        ]
        if typed:
            return typed
        if normalized_subject in _KNOWN_UNIT_TYPE_NAMES:
            return []
        cap = _leading_count(subject)
        type_token = _unit_type_token(subject)
        if type_token is not None:
            matching = [
                unit for unit in units if _entity_type_name(unit) == type_token
            ]
            if type_token == SC2_WORKER_TYPE_NAME:
                return matching[: cap if cap is not None else 1]
            return matching[:cap] if cap is not None else matching
        if (
            _WORKER_KEYWORD in normalized_subject
            or SC2_WORKER_TYPE_NAME in normalized_subject
        ):
            workers = [
                unit
                for unit in units
                if _entity_type_name(unit) == SC2_WORKER_TYPE_NAME
            ]
            return workers[: cap if cap is not None else 1]
        army = [
            unit
            for unit in units
            if (name := _entity_type_name(unit)) is not None
            and name != SC2_WORKER_TYPE_NAME
        ]
        if cap is not None:
            army = army[:cap]
        return army

    def _worker_pool(self) -> list[object]:
        """Return idle workers when any exist, falling back to all workers."""

        workers = getattr(self.bot, "workers", None)
        if workers is None:
            return []
        idle = _materialize(getattr(workers, "idle", None))
        if idle:
            return idle
        return _materialize(workers)

    def _ready_idle_structures(self) -> list[object]:
        """Return ready idle own structures, preferring ``.ready.idle`` chains."""

        group = getattr(self.bot, "structures", None)
        if group is None:
            return []
        ready_attr = getattr(group, "ready", None)
        idle_attr = getattr(ready_attr, "idle", None) if ready_attr is not None else None
        if idle_attr is not None:
            return _materialize(idle_attr)
        return [
            entry
            for entry in _materialize(group)
            if _truthy_flag(entry, "is_ready", default=True)
            and _truthy_flag(entry, "is_idle", default=True)
        ]

    def _resource_target(self, resource: str) -> object | None:
        """Find the gather target unit nearest the own start location.

        Gas gathering requires a *completed* own gas structure: the live game
        silently rejects gather orders on a bare geyser or an in-construction
        refinery, so the adapter refuses (``None``) instead of issuing dead
        orders that would be narrated as success.
        """

        normalized = str(resource).strip().lower()
        anchor = _entity_point(getattr(self.bot, "start_location", None))
        if normalized in SC2_MINERAL_RESOURCE_NAMES:
            candidates = _materialize(getattr(self.bot, "mineral_field", None))
        elif normalized in SC2_GAS_RESOURCE_NAMES:
            candidates = [
                structure
                for structure in _materialize(getattr(self.bot, "structures", None))
                if _entity_type_name(structure) in SC2_GAS_STRUCTURE_TYPE_NAMES
                and _truthy_flag(structure, "is_ready", default=True)
            ]
        else:
            return None
        return _nearest_entity(candidates, anchor)

    def _find_damaged_repair_target(self, target: str) -> object | None:
        """Find the first damaged own structure (then unit) matching loosely."""

        normalized_target = _normalized_name(target)
        generic = (
            normalized_target in SC2_GENERIC_REPAIR_TARGET_NAMES
            if normalized_target is not None
            else False
        )
        structures = _materialize(getattr(self.bot, "structures", None))
        for structure in structures:
            if not _is_damaged(structure):
                continue
            if generic or _loose_name_match(
                _entity_type_name(structure), normalized_target
            ):
                return structure
        if generic:
            return None
        for unit in _materialize(getattr(self.bot, "units", None)):
            if not _is_damaged(unit):
                continue
            if _loose_name_match(_entity_type_name(unit), normalized_target):
                return unit
        return None


class MissingPythonSC2Error(RuntimeError):
    """Raised when UnitTypeId resolution needs python-sc2 but it is absent."""


async def _call_bot_operation(
    operation: Callable[..., object],
    *args: object,
    **kwargs: object,
) -> bool:
    """Call one bot-level operation, treating ``None``/truthy results as done."""

    result = operation(*args, **kwargs)
    if inspect.isawaitable(result):
        result = await result
    return result is None or bool(result)


def _materialize(value: object) -> list[object]:
    """Materialize a Units-like iterable defensively (never strings)."""

    if value is None or isinstance(value, (str, bytes)):
        return []
    if not isinstance(value, Iterable):
        return []
    return list(value)


def _normalized_name(value: object) -> str | None:
    """Uppercase a name, dropping whitespace and underscores, for matching."""

    if type(value) is not str:
        return None
    normalized = "".join(value.split()).replace("_", "").upper()
    return normalized or None


def _entity_type_name(entity: object) -> str | None:
    """Read the normalized type name from ``.name`` or ``.type_id.name``."""

    name = _normalized_name(getattr(entity, "name", None))
    if name is not None:
        return name
    type_id = getattr(entity, "type_id", None)
    if type_id is None:
        return None
    return _normalized_name(getattr(type_id, "name", None))


def _loose_name_match(name: str | None, target: str | None) -> bool:
    """Match normalized names loosely: equality or substring either way."""

    if name is None or target is None:
        return False
    return name == target or name in target or target in name


def _leading_count(text: str) -> int | None:
    """Parse a leading integer from free text such as ``2 Marines``."""

    match = _LEADING_COUNT_PATTERN.match(text)
    if match is None:
        return None
    return int(match.group(1))


def _unit_type_token(subject: str) -> str | None:
    """Extract the known unit-type name from a counted or plural phrase.

    ``6 Marines`` / ``1 Marine`` / ``Marines`` all resolve to ``MARINE``;
    phrases that do not name a single known unit type (``available combat
    units``) return ``None`` so callers can fall back to generic selection.
    """

    match = _LEADING_COUNT_PATTERN.match(subject)
    remainder = subject[match.end() :] if match is not None else subject
    normalized = _normalized_name(remainder)
    if normalized is None:
        return None
    if normalized in _KNOWN_UNIT_TYPE_NAMES:
        return normalized
    if normalized.endswith("S") and normalized[:-1] in _KNOWN_UNIT_TYPE_NAMES:
        return normalized[:-1]
    return None


def _refusal_report(requested: int | None, detail: str) -> SC2ActionReport:
    """Build the structured report for an action refused with nothing issued."""

    return SC2ActionReport(
        applied=False,
        requested_count=requested if requested is not None and requested >= 0 else None,
        issued_count=0,
        detail=detail,
    )


def _issuance_report(
    requested: int | None,
    issued: int,
    shortfall_detail: str,
) -> SC2ActionReport:
    """Build the structured report for counted order issuance.

    ``issued == 0`` is an honest refusal; ``issued`` below a known requested
    count is a partial application annotated with ``shortfall_detail``.
    """

    if issued <= 0:
        return _refusal_report(requested, shortfall_detail)
    partial = requested is not None and issued < requested
    return SC2ActionReport(
        applied=True,
        requested_count=requested,
        issued_count=issued,
        detail=shortfall_detail if partial else "",
    )


def _point_is_taken(
    point: MapPoint | None,
    taken_points: Sequence[MapPoint],
    *,
    radius: float = 1.5,
) -> bool:
    """Return whether a geyser point already hosts an own gas structure."""

    if point is None:
        return False
    return any(point.distance_to(taken) <= radius for taken in taken_points)


def _truthy_flag(entity: object, attribute: str, *, default: bool) -> bool:
    """Read a boolean-ish unit flag, defaulting when the attribute is absent."""

    value = getattr(entity, attribute, _UNSET)
    if value is _UNSET:
        return default
    return bool(value)


def _is_real_number(value: object) -> bool:
    """Return whether a value is a finite real number (bool excluded)."""

    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _entity_point(candidate: object) -> MapPoint | None:
    """Duck-type one Point2/Unit-like object into a :class:`MapPoint`."""

    if candidate is None:
        return None
    if isinstance(candidate, MapPoint):
        return candidate
    x = getattr(candidate, "x", None)
    y = getattr(candidate, "y", None)
    if _is_real_number(x) and _is_real_number(y):
        return MapPoint(float(x), float(y))
    position = getattr(candidate, "position", None)
    if position is not None and position is not candidate:
        x = getattr(position, "x", None)
        y = getattr(position, "y", None)
        if _is_real_number(x) and _is_real_number(y):
            return MapPoint(float(x), float(y))
    if isinstance(candidate, (tuple, list)) and len(candidate) == 2:
        x, y = candidate
        if _is_real_number(x) and _is_real_number(y):
            return MapPoint(float(x), float(y))
    return None


def _nearest_entity(
    entities: Sequence[object],
    anchor: MapPoint | None,
) -> object | None:
    """Pick the entity nearest the anchor with a deterministic tie-break."""

    pointed = [
        (point, entity)
        for entity in entities
        if (point := _entity_point(entity)) is not None
    ]
    if not pointed:
        return entities[0] if entities else None
    if anchor is None:
        return pointed[0][1]
    return min(
        pointed,
        key=lambda pair: (anchor.distance_to(pair[0]), pair[0].x, pair[0].y),
    )[1]


def _is_damaged(entity: object) -> bool:
    """Return whether an entity reports less than full health."""

    health = getattr(entity, "health", None)
    health_max = getattr(entity, "health_max", None)
    if _is_real_number(health) and _is_real_number(health_max):
        return float(health) < float(health_max)
    percentage = getattr(entity, "health_percentage", None)
    if _is_real_number(percentage):
        return float(percentage) < 1.0
    return False


def _game_point(point: MapPoint) -> object:
    """Convert to a python-sc2 ``Point2`` when available, else pass through."""

    try:
        from sc2.position import Point2
    except ImportError:
        return point
    return Point2((point.x, point.y))


SC2UnitTypeResolver = Callable[[str], Any]
"""Public alias for injectable UnitTypeId-name resolver callables."""
