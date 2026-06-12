import json
import unittest

from starcraft_commander.state_resolver import (
    DEFAULT_SC2_STATE_RESOLVER,
    SC2CommanderState,
    SC2StateResolver,
    SC2StateResolverInterface,
    resolve_commander_state,
)


class FakeTypeId:
    def __init__(self, name: str) -> None:
        self.name = name


class FakeUnit:
    """Plain duck-typed unit; attributes only exist when explicitly given."""

    def __init__(
        self,
        name: str | None = None,
        type_id_name: str | None = None,
        is_ready: bool | None = None,
        is_idle: bool | None = None,
    ) -> None:
        if name is not None:
            self.name = name
        if type_id_name is not None:
            self.type_id = FakeTypeId(type_id_name)
        if is_ready is not None:
            self.is_ready = is_ready
        if is_idle is not None:
            self.is_idle = is_idle


class FakeIdleWorkers:
    def __init__(self, count: int) -> None:
        self._count = count

    def __len__(self) -> int:
        return self._count


class FakeWorkers:
    def __init__(self, idle_count: int) -> None:
        self.idle = FakeIdleWorkers(idle_count)


class FakeGameState:
    def __init__(self, game_loop: int) -> None:
        self.game_loop = game_loop


class FakeBot:
    """Minimal complete python-sc2 BotAI-like fake (no python-sc2 needed)."""

    def __init__(self) -> None:
        self.minerals = 0
        self.vespene = 0
        self.supply_used = 0
        self.supply_cap = 0
        self.supply_left = 0
        self.units = []
        self.structures = []
        self.enemy_units = []
        self.enemy_structures = []
        self.workers = FakeWorkers(idle_count=0)
        self.supply_army = 0
        self.state = FakeGameState(game_loop=0)
        self.time = 0.0


def build_complete_terran_bot() -> FakeBot:
    bot = FakeBot()
    bot.minerals = 400
    bot.vespene = 125
    bot.supply_used = 30
    bot.supply_cap = 39
    bot.supply_left = 9
    bot.units = [
        *(FakeUnit(name="SCV", is_idle=False) for _ in range(12)),
        *(FakeUnit(type_id_name="Marine") for _ in range(6)),
    ]
    bot.structures = [
        *(FakeUnit(name="Command Center", is_ready=True) for _ in range(2)),
        *(FakeUnit(name="Barracks", is_ready=True) for _ in range(2)),
        FakeUnit(type_id_name="FACTORY", is_ready=True),
        *(FakeUnit(type_id_name="SupplyDepot", is_ready=True) for _ in range(3)),
        FakeUnit(type_id_name="SupplyDepot", is_ready=False),
    ]
    bot.enemy_units = [FakeUnit(name="Zergling") for _ in range(4)]
    bot.enemy_structures = [FakeUnit(name="Hatchery")]
    bot.workers = FakeWorkers(idle_count=2)
    bot.supply_army = 6
    bot.state = FakeGameState(game_loop=672)
    bot.time = 30.0
    return bot


class WeirdBot:
    """Hostile bot object: raising properties and unusable attribute values."""

    vespene = -5
    supply_used = "thirty"
    units = 42
    structures = "weird"
    time = -3.5

    @property
    def minerals(self) -> int:
        raise RuntimeError("observation backend exploded")

    @property
    def workers(self) -> object:
        raise RuntimeError("no worker view")


class SC2CommanderStateContractTest(unittest.TestCase):
    def test_non_negative_int_fields_reject_negative_values(self) -> None:
        for field_name in (
            "minerals",
            "vespene",
            "supply_used",
            "supply_cap",
            "supply_left",
            "idle_worker_count",
            "army_count",
            "game_loop",
        ):
            with self.subTest(field_name=field_name):
                with self.assertRaises(ValueError):
                    SC2CommanderState(**{field_name: -1})

    def test_game_time_seconds_rejects_negative_and_coerces_to_float(self) -> None:
        with self.assertRaises(ValueError):
            SC2CommanderState(game_time_seconds=-0.5)
        state = SC2CommanderState(game_time_seconds=12)
        self.assertIsInstance(state.game_time_seconds, float)
        self.assertEqual(12.0, state.game_time_seconds)

    def test_count_mappings_reject_negative_counts(self) -> None:
        for mapping_name in (
            "own_units",
            "own_structures",
            "structures_in_progress",
            "visible_enemy_units",
            "visible_enemy_structures",
        ):
            with self.subTest(mapping_name=mapping_name):
                with self.assertRaises(ValueError):
                    SC2CommanderState(**{mapping_name: {"MARINE": -1}})

    def test_observation_complete_derives_from_notes(self) -> None:
        self.assertTrue(SC2CommanderState().observation_complete)
        degraded = SC2CommanderState(observation_notes=("bot.minerals is missing.",))
        self.assertFalse(degraded.observation_complete)

    def test_to_dict_is_json_ready_and_round_trips(self) -> None:
        state = SC2CommanderState(
            minerals=400,
            vespene=125,
            supply_used=30,
            supply_cap=39,
            supply_left=9,
            own_units={"MARINE": 6, "SCV": 12},
            own_structures={"BARRACKS": 2, "COMMANDCENTER": 2},
            structures_in_progress={"SUPPLYDEPOT": 1},
            visible_enemy_units={"ZERGLING": 4},
            visible_enemy_structures={"HATCHERY": 1},
            idle_worker_count=2,
            army_count=6,
            game_loop=672,
            game_time_seconds=30.0,
            observation_notes=(),
        )
        payload = state.to_dict()
        round_tripped = json.loads(json.dumps(payload))
        self.assertEqual(payload, round_tripped)
        self.assertEqual(
            {
                "minerals": 400,
                "vespene": 125,
                "supply_used": 30,
                "supply_cap": 39,
                "supply_left": 9,
                "own_units": {"MARINE": 6, "SCV": 12},
                "own_structures": {"BARRACKS": 2, "COMMANDCENTER": 2},
                "structures_in_progress": {"SUPPLYDEPOT": 1},
                "visible_enemy_units": {"ZERGLING": 4},
                "visible_enemy_structures": {"HATCHERY": 1},
                "idle_worker_count": 2,
                "army_count": 6,
                "game_loop": 672,
                "game_time_seconds": 30.0,
                "observation_notes": [],
                "observation_complete": True,
            },
            payload,
        )


class SC2StateResolverTerranTest(unittest.TestCase):
    def setUp(self) -> None:
        self.resolver = SC2StateResolver()

    def test_default_resolver_satisfies_protocol(self) -> None:
        self.assertIsInstance(self.resolver, SC2StateResolverInterface)
        self.assertIsInstance(DEFAULT_SC2_STATE_RESOLVER, SC2StateResolverInterface)

    def test_resolves_complete_terran_state_with_exact_counts(self) -> None:
        state = self.resolver.resolve(build_complete_terran_bot())
        expected = {
            "minerals": 400,
            "vespene": 125,
            "supply_used": 30,
            "supply_cap": 39,
            "supply_left": 9,
            "own_units": {"MARINE": 6, "SCV": 12},
            "own_structures": {
                "BARRACKS": 2,
                "COMMANDCENTER": 2,
                "FACTORY": 1,
                "SUPPLYDEPOT": 3,
            },
            "structures_in_progress": {"SUPPLYDEPOT": 1},
            "visible_enemy_units": {"ZERGLING": 4},
            "visible_enemy_structures": {"HATCHERY": 1},
            "idle_worker_count": 2,
            "army_count": 6,
            "game_loop": 672,
            "game_time_seconds": 30.0,
        }
        for field_name, expected_value in expected.items():
            with self.subTest(field_name=field_name):
                self.assertEqual(expected_value, getattr(state, field_name))

    def test_complete_fake_yields_complete_observation(self) -> None:
        state = self.resolver.resolve(build_complete_terran_bot())
        self.assertEqual((), state.observation_notes)
        self.assertTrue(state.observation_complete)

    def test_structure_type_names_are_uppercased_without_spaces(self) -> None:
        bot = FakeBot()
        bot.structures = [
            FakeUnit(name="Command Center", is_ready=True),
            FakeUnit(type_id_name="Supply Depot", is_ready=True),
        ]
        state = self.resolver.resolve(bot)
        self.assertEqual({"COMMANDCENTER": 1, "SUPPLYDEPOT": 1}, state.own_structures)

    def test_splits_ready_and_in_progress_structures(self) -> None:
        bot = FakeBot()
        bot.structures = [
            FakeUnit(name="Barracks", is_ready=True),
            FakeUnit(name="Barracks", is_ready=False),
            FakeUnit(name="Supply Depot", is_ready=False),
            FakeUnit(name="Command Center", is_ready=True),
            FakeUnit(name="Bunker"),
        ]
        state = self.resolver.resolve(bot)
        self.assertEqual(
            {"BARRACKS": 1, "BUNKER": 1, "COMMANDCENTER": 1},
            state.own_structures,
            "missing is_ready must default to ready",
        )
        self.assertEqual(
            {"BARRACKS": 1, "SUPPLYDEPOT": 1},
            state.structures_in_progress,
        )
        self.assertTrue(state.observation_complete)

    def test_resolves_visible_enemy_units_and_structures(self) -> None:
        bot = FakeBot()
        bot.enemy_units = [
            FakeUnit(name="Zergling"),
            FakeUnit(name="Zergling"),
            FakeUnit(type_id_name="Roach"),
        ]
        bot.enemy_structures = [
            FakeUnit(name="Hatchery"),
            FakeUnit(name="Spawning Pool"),
        ]
        state = self.resolver.resolve(bot)
        self.assertEqual({"ROACH": 1, "ZERGLING": 2}, state.visible_enemy_units)
        self.assertEqual(
            {"HATCHERY": 1, "SPAWNINGPOOL": 1},
            state.visible_enemy_structures,
        )


class SC2StateResolverDegradationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.resolver = SC2StateResolver()

    def test_negative_supply_left_is_normal_state_not_observation_failure(self) -> None:
        # Real python-sc2 reports supply_left = supply_cap - supply_used,
        # which goes negative after losing depots while supply blocked. That
        # is valid game state: it must clamp silently, keep the observation
        # complete, and never gate every mutating command.
        bot = build_complete_terran_bot()
        bot.supply_used = 52
        bot.supply_cap = 50
        bot.supply_left = -2

        state = self.resolver.resolve(bot)

        self.assertEqual(0, state.supply_left)
        self.assertEqual((), state.observation_notes)
        self.assertTrue(state.observation_complete)

    def test_empty_bot_degrades_to_zero_state_with_notes(self) -> None:
        class EmptyBot:
            pass

        state = self.resolver.resolve(EmptyBot())
        self.assertEqual(0, state.minerals)
        self.assertEqual(0, state.vespene)
        self.assertEqual(0, state.supply_used)
        self.assertEqual(0, state.supply_cap)
        self.assertEqual(0, state.supply_left)
        self.assertEqual({}, state.own_units)
        self.assertEqual({}, state.own_structures)
        self.assertEqual({}, state.structures_in_progress)
        self.assertEqual({}, state.visible_enemy_units)
        self.assertEqual({}, state.visible_enemy_structures)
        self.assertEqual(0, state.idle_worker_count)
        self.assertEqual(0, state.army_count)
        self.assertEqual(0, state.game_loop)
        self.assertEqual(0.0, state.game_time_seconds)
        self.assertFalse(state.observation_complete)
        for attribute_name in (
            "bot.minerals",
            "bot.vespene",
            "bot.supply_used",
            "bot.supply_cap",
            "bot.supply_left",
            "bot.units",
            "bot.structures",
            "bot.enemy_units",
            "bot.enemy_structures",
            "bot.workers",
            "bot.supply_army",
            "bot.state",
            "bot.time",
        ):
            with self.subTest(attribute_name=attribute_name):
                self.assertTrue(
                    any(attribute_name in note for note in state.observation_notes),
                    f"expected a note naming {attribute_name}: {state.observation_notes}",
                )

    def test_weird_bot_never_raises_and_records_notes(self) -> None:
        state = self.resolver.resolve(WeirdBot())
        self.assertEqual(0, state.minerals)
        self.assertEqual(0, state.vespene, "negative vespene must clamp to 0")
        self.assertEqual(0, state.supply_used, "non-numeric supply must default to 0")
        self.assertEqual({}, state.own_units, "non-iterable units must default empty")
        self.assertEqual({}, state.own_structures, "str structures must default empty")
        self.assertEqual(0.0, state.game_time_seconds, "negative time must clamp")
        self.assertFalse(state.observation_complete)
        for attribute_name in (
            "bot.minerals",
            "bot.vespene",
            "bot.supply_used",
            "bot.units",
            "bot.structures",
            "bot.workers",
            "bot.time",
        ):
            with self.subTest(attribute_name=attribute_name):
                self.assertTrue(
                    any(attribute_name in note for note in state.observation_notes),
                    f"expected a note naming {attribute_name}: {state.observation_notes}",
                )

    def test_non_resolver_objects_never_raise(self) -> None:
        for bot in (None, 7, "bot", object(), [], {}):
            with self.subTest(bot=repr(bot)):
                state = self.resolver.resolve(bot)
                self.assertIsInstance(state, SC2CommanderState)
                self.assertFalse(state.observation_complete)

    def test_idle_workers_fall_back_to_idle_scv_count(self) -> None:
        bot = FakeBot()
        del bot.workers
        bot.units = [
            FakeUnit(name="SCV", is_idle=True),
            FakeUnit(name="SCV", is_idle=True),
            FakeUnit(name="SCV", is_idle=False),
            FakeUnit(name="SCV"),
            FakeUnit(type_id_name="Marine", is_idle=True),
        ]
        state = self.resolver.resolve(bot)
        self.assertEqual(2, state.idle_worker_count)
        self.assertTrue(any("bot.workers" in note for note in state.observation_notes))
        self.assertFalse(state.observation_complete)

    def test_army_count_falls_back_to_non_scv_own_units(self) -> None:
        bot = FakeBot()
        del bot.supply_army
        bot.units = [
            *(FakeUnit(name="SCV", is_idle=False) for _ in range(8)),
            *(FakeUnit(name="Marine") for _ in range(5)),
            FakeUnit(type_id_name="Hellion"),
        ]
        state = self.resolver.resolve(bot)
        self.assertEqual(6, state.army_count)
        self.assertTrue(
            any("bot.supply_army" in note for note in state.observation_notes)
        )
        self.assertFalse(state.observation_complete)

    def test_unit_entry_without_readable_type_name_is_skipped_with_note(self) -> None:
        bot = FakeBot()
        bot.units = [FakeUnit(name="SCV", is_idle=False), object()]
        state = self.resolver.resolve(bot)
        self.assertEqual({"SCV": 1}, state.own_units)
        self.assertTrue(
            any("bot.units" in note and "skipped" in note for note in state.observation_notes)
        )

    def test_resolved_state_to_dict_is_json_ready(self) -> None:
        state = self.resolver.resolve(build_complete_terran_bot())
        payload = state.to_dict()
        self.assertEqual(payload, json.loads(json.dumps(payload)))
        self.assertTrue(payload["observation_complete"])


class ResolveCommanderStateConvenienceTest(unittest.TestCase):
    def test_module_level_function_delegates_to_default_resolver(self) -> None:
        bot = build_complete_terran_bot()
        self.assertEqual(
            DEFAULT_SC2_STATE_RESOLVER.resolve(bot).to_dict(),
            resolve_commander_state(bot).to_dict(),
        )

    def test_default_resolver_is_the_default_implementation(self) -> None:
        self.assertIsInstance(DEFAULT_SC2_STATE_RESOLVER, SC2StateResolver)



if __name__ == "__main__":
    unittest.main()
