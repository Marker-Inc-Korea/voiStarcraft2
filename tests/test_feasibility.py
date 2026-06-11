import unittest

import toycraft_commander as package_exports
from toycraft_commander.feasibility import (
    ConstructionOrder,
    INTENT_FEASIBILITY_RULES,
    PHASE_ZERO_STRUCTURE_COSTS,
    PHASE_ZERO_STRUCTURE_NAMES,
    PRODUCTION_QUEUE_CAPACITY_PER_PRODUCER,
    ToyCraftState,
    get_intent_feasibility_rule,
    validate_intent_feasibility,
)
from toycraft_commander.intents import (
    CANONICAL_INTENT_NAMES,
    BuildStructureIntent,
    DefendIntent,
    ExpandIntent,
    FeasibilityErrorReason,
    GatherResourceIntent,
    HarassIntent,
    RepairIntent,
    ScoutIntent,
    SummarizeStateIntent,
    TrainArmyIntent,
    TrainWorkerIntent,
    ValidationStatus,
)
from toycraft_commander.resources import ResourceState, SupplyState


def ready_state() -> ToyCraftState:
    return ToyCraftState(
        resources=ResourceState(minerals=1000, gas=200),
        supply=SupplyState(used_supply=8, supply_capacity=30),
        units={"SCV": 8, "Marine": 6, "Vulture": 1},
        structures={
            "Command Center": 1,
            "Supply Depot": 1,
            "Barracks": 1,
            "Refinery": 1,
            "Bunker": 1,
        },
        claimed_locations=("main", "main base"),
        damaged_targets=("front bunker",),
    )


def executable_intent_feasibility_cases():
    return (
        (
            "GATHER_RESOURCE",
            GatherResourceIntent(resource="minerals", worker_count=2, base="main"),
            ready_state(),
        ),
        (
            "BUILD_STRUCTURE",
            BuildStructureIntent(structure="Bunker", location="natural choke"),
            ready_state(),
        ),
        ("TRAIN_WORKER", TrainWorkerIntent(count=1), ready_state()),
        ("TRAIN_ARMY", TrainArmyIntent(unit_type="Marine", count=1), ready_state()),
        ("SCOUT", ScoutIntent(target="enemy natural", unit_group="1 SCV"), ready_state()),
        ("SUMMARIZE_STATE", SummarizeStateIntent(), ready_state()),
        ("DEFEND", DefendIntent(location="main ramp", unit_group="2 Marines"), ready_state()),
        ("REPAIR", RepairIntent(target="front bunker", worker_count=1), ready_state()),
        ("EXPAND", ExpandIntent(location="natural expansion"), ready_state()),
        ("HARASS", HarassIntent(target="enemy mineral line", unit_group="2 Marines"), ready_state()),
    )


def raw_feasibility_blocking_cases():
    return (
        (
            "resources",
            TrainWorkerIntent(count=2),
            ToyCraftState(
                resources=ResourceState(minerals=75, gas=0),
                supply=SupplyState(used_supply=4, supply_capacity=15),
                units={"SCV": 4},
                structures={"Command Center": 1},
            ),
            FeasibilityErrorReason.INSUFFICIENT_MINERALS,
            "Need 25 more minerals",
        ),
        (
            "unit availability",
            ScoutIntent(target="enemy natural", unit_group="2 Marines"),
            ToyCraftState(
                resources=ResourceState(minerals=500),
                units={"SCV": 4, "Marine": 1},
                structures={"Command Center": 1},
            ),
            FeasibilityErrorReason.UNAVAILABLE_UNIT_GROUP,
            "No available units match",
        ),
        (
            "target validity",
            HarassIntent(target="main ramp", unit_group="2 Marines"),
            ready_state(),
            FeasibilityErrorReason.INVALID_TARGET,
            "not an enemy harassment target",
        ),
        (
            "rule preconditions",
            BuildStructureIntent(structure="Bunker", location="natural choke"),
            ToyCraftState(
                resources=ResourceState(minerals=500, gas=0),
                supply=SupplyState(used_supply=4, supply_capacity=15),
                units={"SCV": 4},
                structures={"Command Center": 1},
            ),
            FeasibilityErrorReason.MISSING_PREREQUISITE,
            "requires a completed Barracks",
        ),
    )


class ToyCraftFeasibilityStateTest(unittest.TestCase):
    def test_state_defaults_model_opening_snapshot(self) -> None:
        state = ToyCraftState()

        self.assertEqual({"minerals": 50, "gas": 0}, state.resources.to_dict())
        self.assertEqual({"used_supply": 4, "supply_capacity": 15}, state.supply.to_dict())
        self.assertEqual(4, state.available_worker_count())
        self.assertEqual(1, state.available_producer_count("Command Center"))
        self.assertEqual(
            PRODUCTION_QUEUE_CAPACITY_PER_PRODUCER,
            state.available_production_queue_slots("Command Center"),
        )
        self.assertTrue(state.has_structure("Command Center"))

    def test_state_normalizes_aliases_and_rejects_impossible_counts(self) -> None:
        state = ToyCraftState(
            units={"marines": 2, "마린": 1},
            structures={"commandcenter": 1, "bunker": 1},
            production_queues={"commandcenter": 2},
            construction_queue=(
                {
                    "structure_name": "supplydepot",
                    "location": "main ramp",
                    "remaining_seconds": 30,
                },
            ),
        )

        self.assertEqual(3, state.unit_count("Marine"))
        self.assertEqual(1, state.structure_count("Command Center"))
        self.assertEqual(1, state.structure_count("Bunker"))
        self.assertEqual(2, state.queued_production_count("Command Center"))
        self.assertEqual(1, state.construction_count("Supply Depot"))
        self.assertEqual(
            (
                ConstructionOrder(
                    structure_name="Supply Depot",
                    location="main ramp",
                    remaining_seconds=30,
                    assigned_workers=1,
                ),
            ),
            state.construction_queue,
        )

        with self.assertRaisesRegex(ValueError, "SCV must be a non-negative integer"):
            ToyCraftState(units={"SCV": -1})
        with self.assertRaisesRegex(ValueError, "Unsupported ToyCraft state structure"):
            ToyCraftState(structures={"Starport": 1})
        with self.assertRaisesRegex(
            ValueError,
            "Unsupported ToyCraft construction structure",
        ):
            ToyCraftState(
                construction_queue=(
                    {
                        "structure_name": "Starport",
                        "location": "main",
                        "remaining_seconds": 60,
                    },
                )
            )


class IntentFeasibilityDispatchTest(unittest.TestCase):
    def test_dispatch_table_covers_exactly_the_ten_canonical_intents(self) -> None:
        self.assertEqual(set(CANONICAL_INTENT_NAMES), set(INTENT_FEASIBILITY_RULES))
        self.assertEqual(10, len(INTENT_FEASIBILITY_RULES))

        for intent in CANONICAL_INTENT_NAMES:
            with self.subTest(intent=intent):
                self.assertIs(INTENT_FEASIBILITY_RULES[intent], get_intent_feasibility_rule(intent))

    def test_package_exports_feasibility_surface(self) -> None:
        self.assertIs(ToyCraftState, package_exports.ToyCraftState)
        self.assertIs(INTENT_FEASIBILITY_RULES, package_exports.INTENT_FEASIBILITY_RULES)
        self.assertIs(validate_intent_feasibility, package_exports.validate_intent_feasibility)
        self.assertIs(get_intent_feasibility_rule, package_exports.get_intent_feasibility_rule)
        self.assertEqual(PHASE_ZERO_STRUCTURE_NAMES, package_exports.PHASE_ZERO_STRUCTURE_NAMES)
        self.assertEqual(PHASE_ZERO_STRUCTURE_COSTS, package_exports.PHASE_ZERO_STRUCTURE_COSTS)
        self.assertEqual(
            PRODUCTION_QUEUE_CAPACITY_PER_PRODUCER,
            package_exports.PRODUCTION_QUEUE_CAPACITY_PER_PRODUCER,
        )

    def test_invalid_raw_payload_returns_shared_payload_validation_result(self) -> None:
        result = validate_intent_feasibility(
            {"intent": "TRAIN_ARMY", "priority": "normal", "constraints": []},
            ready_state(),
        )

        self.assertFalse(result.executable)
        self.assertEqual(ValidationStatus.REJECTED, result.status)
        self.assertEqual(FeasibilityErrorReason.MISSING_REQUIRED_FIELD, result.reason_code)
        self.assertIn("unit_type", result.missing_fields)


class EconomyProductionFeasibilityTest(unittest.TestCase):
    def test_all_core_economy_and_production_intents_can_pass_when_state_allows(self) -> None:
        state = ready_state()
        payloads = (
            GatherResourceIntent(resource="minerals", worker_count=3, base="main"),
            BuildStructureIntent(structure="Supply Depot", location="main ramp"),
            BuildStructureIntent(structure="Bunker", location="natural choke"),
            TrainWorkerIntent(count=1),
            TrainArmyIntent(unit_type="Marine", count=2),
            ExpandIntent(location="natural expansion"),
        )

        for payload in payloads:
            with self.subTest(intent=payload.intent, payload=payload):
                result = validate_intent_feasibility(payload, state)

                self.assertTrue(result.executable)
                self.assertEqual(ValidationStatus.EXECUTABLE, result.status)
                self.assertEqual(payload, result.payload)
                self.assertEqual((), result.issues)

    def test_production_rejections_include_typed_reasons_and_alternatives(self) -> None:
        state = ToyCraftState(
            resources=ResourceState(minerals=25, gas=0),
            supply=SupplyState(used_supply=15, supply_capacity=15),
            units={"SCV": 4},
            structures={"Command Center": 1},
        )

        army_result = validate_intent_feasibility(
            TrainArmyIntent(unit_type="Marine", count=2),
            state,
        )
        self.assertFalse(army_result.executable)
        self.assertEqual(
            (
                FeasibilityErrorReason.UNAVAILABLE_PRODUCER,
                FeasibilityErrorReason.INSUFFICIENT_MINERALS,
                FeasibilityErrorReason.INSUFFICIENT_SUPPLY,
            ),
            army_result.reason_codes,
        )
        self.assertTrue(army_result.alternative.strip())

        barracks_result = validate_intent_feasibility(
            BuildStructureIntent(structure="Barracks", location="main base"),
            ToyCraftState(resources=ResourceState(minerals=500), units={"SCV": 4}),
        )
        self.assertFalse(barracks_result.executable)
        self.assertEqual(FeasibilityErrorReason.MISSING_PREREQUISITE, barracks_result.reason_code)
        self.assertIn("Supply Depot", barracks_result.reason)

    def test_location_worker_and_constraint_conflicts_block_execution(self) -> None:
        state = ToyCraftState(
            resources=ResourceState(minerals=1000),
            units={"SCV": 1},
            busy_workers=1,
            structures={"Command Center": 1},
        )
        result = validate_intent_feasibility(
            BuildStructureIntent(
                priority="high",
                constraints=("spend minerals but save minerals",),
                structure="Supply Depot",
                location="enemy front",
            ),
            state,
        )

        self.assertFalse(result.executable)
        self.assertIn(FeasibilityErrorReason.CONSTRAINT_CONFLICT, result.reason_codes)
        self.assertIn(FeasibilityErrorReason.LOCATION_UNAVAILABLE, result.reason_codes)
        self.assertIn(FeasibilityErrorReason.UNAVAILABLE_WORKER, result.reason_codes)

    def test_gas_gathering_requires_refinery(self) -> None:
        result = validate_intent_feasibility(
            GatherResourceIntent(resource="gas", worker_count=1, base="main"),
            ToyCraftState(units={"SCV": 4}, structures={"Command Center": 1}),
        )

        self.assertFalse(result.executable)
        self.assertEqual(FeasibilityErrorReason.MISSING_PREREQUISITE, result.reason_code)
        self.assertIn("Refinery", result.reason)

    def test_resource_spending_blocks_unaffordable_economy_and_production_orders(self) -> None:
        state = ToyCraftState(
            resources=ResourceState(minerals=75, gas=0),
            supply=SupplyState(used_supply=4, supply_capacity=15),
            units={"SCV": 4},
            structures={"Command Center": 1, "Supply Depot": 1},
        )

        depot_result = validate_intent_feasibility(
            BuildStructureIntent(structure="Supply Depot", location="main ramp"),
            state,
        )
        worker_result = validate_intent_feasibility(TrainWorkerIntent(count=2), state)

        self.assertFalse(depot_result.executable)
        self.assertEqual(FeasibilityErrorReason.INSUFFICIENT_MINERALS, depot_result.reason_code)
        self.assertIn("Need 25 more minerals", depot_result.reason)
        self.assertFalse(worker_result.executable)
        self.assertEqual(FeasibilityErrorReason.INSUFFICIENT_MINERALS, worker_result.reason_code)
        self.assertIn("Need 25 more minerals", worker_result.reason)

    def test_supply_limit_blocks_training_without_blocking_supply_depot_construction(self) -> None:
        state = ToyCraftState(
            resources=ResourceState(minerals=200, gas=0),
            supply=SupplyState(used_supply=15, supply_capacity=15),
            units={"SCV": 4},
            structures={"Command Center": 1},
        )

        worker_result = validate_intent_feasibility(TrainWorkerIntent(count=1), state)
        depot_result = validate_intent_feasibility(
            BuildStructureIntent(structure="Supply Depot", location="main ramp"),
            state,
        )

        self.assertFalse(worker_result.executable)
        self.assertEqual(FeasibilityErrorReason.INSUFFICIENT_SUPPLY, worker_result.reason_code)
        self.assertTrue(depot_result.executable)

    def test_building_prerequisites_block_locked_terran_structures(self) -> None:
        barracks_result = validate_intent_feasibility(
            BuildStructureIntent(structure="Barracks", location="main base"),
            ToyCraftState(resources=ResourceState(minerals=500), structures={"Command Center": 1}),
        )
        bunker_result = validate_intent_feasibility(
            BuildStructureIntent(structure="Bunker", location="natural choke"),
            ToyCraftState(resources=ResourceState(minerals=500), structures={"Command Center": 1}),
        )

        self.assertFalse(barracks_result.executable)
        self.assertEqual(FeasibilityErrorReason.MISSING_PREREQUISITE, barracks_result.reason_code)
        self.assertIn("Supply Depot", barracks_result.reason)
        self.assertFalse(bunker_result.executable)
        self.assertEqual(FeasibilityErrorReason.MISSING_PREREQUISITE, bunker_result.reason_code)
        self.assertIn("Barracks", bunker_result.reason)

    def test_production_queue_availability_blocks_full_queues(self) -> None:
        state = ToyCraftState(
            resources=ResourceState(minerals=500, gas=0),
            supply=SupplyState(used_supply=8, supply_capacity=30),
            units={"SCV": 8, "Marine": 4},
            structures={"Command Center": 1, "Barracks": 1},
            busy_producers={"Barracks": 1},
            production_queues={"Barracks": PRODUCTION_QUEUE_CAPACITY_PER_PRODUCER - 1},
        )

        result = validate_intent_feasibility(TrainArmyIntent(unit_type="Marine", count=1), state)

        self.assertFalse(result.executable)
        self.assertEqual(FeasibilityErrorReason.UNAVAILABLE_PRODUCER, result.reason_code)
        self.assertIn("queue has 0 open slot", result.reason)
        self.assertIn("free queue space", result.alternative)

    def test_busy_producer_with_open_queue_slot_can_accept_production_order(self) -> None:
        state = ToyCraftState(
            resources=ResourceState(minerals=500, gas=0),
            supply=SupplyState(used_supply=8, supply_capacity=30),
            units={"SCV": 8, "Marine": 4},
            structures={"Command Center": 1, "Barracks": 1},
            busy_producers={"Barracks": 1},
            production_queues={"Barracks": 1},
        )

        result = validate_intent_feasibility(TrainArmyIntent(unit_type="Marine", count=2), state)

        self.assertTrue(result.executable)

    def test_construction_placement_rules_reject_wrong_targets_and_occupied_slots(self) -> None:
        build_ready_state = ToyCraftState(
            resources=ResourceState(minerals=1000, gas=200),
            supply=SupplyState(used_supply=8, supply_capacity=30),
            units={"SCV": 8, "Marine": 2},
            structures={"Command Center": 1, "Supply Depot": 1, "Barracks": 1},
        )
        refinery_wrong_target = validate_intent_feasibility(
            BuildStructureIntent(structure="Refinery", location="main ramp"),
            build_ready_state,
        )
        depot_unclaimed_base = validate_intent_feasibility(
            BuildStructureIntent(structure="Supply Depot", location="natural expansion"),
            build_ready_state,
        )
        command_center_claimed_main = validate_intent_feasibility(
            BuildStructureIntent(structure="Command Center", location="main base"),
            build_ready_state,
        )
        duplicate_refinery = validate_intent_feasibility(
            BuildStructureIntent(structure="Refinery", location="main geyser"),
            ToyCraftState(
                resources=ResourceState(minerals=1000, gas=200),
                supply=SupplyState(used_supply=8, supply_capacity=30),
                units={"SCV": 8},
                structures={"Command Center": 1, "Refinery": 1},
            ),
        )
        duplicate_in_progress_depot = validate_intent_feasibility(
            BuildStructureIntent(structure="Supply Depot", location="main ramp"),
            ToyCraftState(
                resources=ResourceState(minerals=1000, gas=200),
                supply=SupplyState(used_supply=8, supply_capacity=30),
                units={"SCV": 8},
                structures={"Command Center": 1},
                construction_queue=(
                    ConstructionOrder(
                        structure_name="Supply Depot",
                        location="main ramp",
                        remaining_seconds=20,
                    ),
                ),
            ),
        )

        self.assertFalse(refinery_wrong_target.executable)
        self.assertEqual(
            FeasibilityErrorReason.LOCATION_UNAVAILABLE,
            refinery_wrong_target.reason_code,
        )
        self.assertIn("main geyser", refinery_wrong_target.alternative)
        self.assertFalse(depot_unclaimed_base.executable)
        self.assertEqual(FeasibilityErrorReason.LOCATION_UNAVAILABLE, depot_unclaimed_base.reason_code)
        self.assertIn("compatible build location", depot_unclaimed_base.alternative)
        self.assertFalse(command_center_claimed_main.executable)
        self.assertEqual(
            FeasibilityErrorReason.LOCATION_UNAVAILABLE,
            command_center_claimed_main.reason_code,
        )
        self.assertIn("already claimed", command_center_claimed_main.reason)
        self.assertFalse(duplicate_refinery.executable)
        self.assertEqual(FeasibilityErrorReason.LOCATION_UNAVAILABLE, duplicate_refinery.reason_code)
        self.assertIn("already has a completed Refinery", duplicate_refinery.reason)
        self.assertFalse(duplicate_in_progress_depot.executable)
        self.assertEqual(
            FeasibilityErrorReason.LOCATION_UNAVAILABLE,
            duplicate_in_progress_depot.reason_code,
        )
        self.assertIn("already under construction", duplicate_in_progress_depot.reason)

    def test_construction_requires_builder_and_completed_base_precondition(self) -> None:
        result = validate_intent_feasibility(
            BuildStructureIntent(structure="Supply Depot", location="main ramp"),
            ToyCraftState(
                resources=ResourceState(minerals=500),
                units={"SCV": 4},
                structures={},
            ),
        )

        self.assertFalse(result.executable)
        self.assertEqual(FeasibilityErrorReason.MISSING_PREREQUISITE, result.reason_code)
        self.assertIn("Command Center", result.reason)


class UnitControlFeasibilityTest(unittest.TestCase):
    def test_unit_control_intents_can_pass_when_targets_and_groups_are_available(self) -> None:
        state = ready_state()
        payloads = (
            ScoutIntent(target="enemy natural", unit_group="1 SCV"),
            SummarizeStateIntent(),
            DefendIntent(location="main ramp", unit_group="available combat units"),
            RepairIntent(target="front bunker", worker_count=2),
            HarassIntent(target="enemy mineral line", unit_group="2 Marines"),
        )

        for payload in payloads:
            with self.subTest(intent=payload.intent):
                self.assertTrue(validate_intent_feasibility(payload, state).executable)

    def test_unit_control_rejects_invalid_targets_and_unavailable_groups(self) -> None:
        state = ToyCraftState(
            resources=ResourceState(minerals=500),
            units={"SCV": 4, "Marine": 1},
            structures={"Command Center": 1},
        )

        harass_result = validate_intent_feasibility(
            HarassIntent(target="main ramp", unit_group="1 Marine"),
            state,
        )
        self.assertFalse(harass_result.executable)
        self.assertEqual(FeasibilityErrorReason.INVALID_TARGET, harass_result.reason_code)

        repair_result = validate_intent_feasibility(
            RepairIntent(target="front bunker", worker_count=1),
            state,
        )
        self.assertFalse(repair_result.executable)
        self.assertEqual(FeasibilityErrorReason.INVALID_TARGET, repair_result.reason_code)

    def test_gathering_requires_workers_at_a_claimed_friendly_base(self) -> None:
        state = ToyCraftState(
            units={"SCV": 4},
            structures={"Command Center": 1},
            claimed_locations=("main",),
        )

        result = validate_intent_feasibility(
            GatherResourceIntent(resource="minerals", worker_count=1, base="natural expansion"),
            state,
        )

        self.assertFalse(result.executable)
        self.assertEqual(FeasibilityErrorReason.LOCATION_UNAVAILABLE, result.reason_code)
        self.assertIn("not a claimed friendly base", result.reason)
        self.assertIn("claimed base", result.alternative)

    def test_unit_selection_rejects_ambiguous_or_enemy_controlled_groups(self) -> None:
        state = ready_state()

        ambiguous_result = validate_intent_feasibility(
            ScoutIntent(target="enemy natural", unit_group="small squad"),
            state,
        )
        enemy_result = validate_intent_feasibility(
            DefendIntent(location="main ramp", unit_group="1 Zealot"),
            state,
        )

        self.assertFalse(ambiguous_result.executable)
        self.assertEqual(
            FeasibilityErrorReason.UNAVAILABLE_UNIT_GROUP,
            ambiguous_result.reason_code,
        )
        self.assertIn("No available units match", ambiguous_result.reason)
        self.assertFalse(enemy_result.executable)
        self.assertEqual(
            FeasibilityErrorReason.UNSUPPORTED_PHASE_ZERO_SCOPE,
            enemy_result.reason_code,
        )
        self.assertIn("enemy-controlled", enemy_result.reason)

    def test_movement_commands_reject_unreachable_or_wrong_side_targets(self) -> None:
        state = ready_state()

        defend_enemy_result = validate_intent_feasibility(
            DefendIntent(location="enemy natural", unit_group="2 Marines"),
            state,
        )
        scout_unknown_result = validate_intent_feasibility(
            ScoutIntent(target="island expansion", unit_group="1 SCV"),
            state,
        )

        self.assertFalse(defend_enemy_result.executable)
        self.assertEqual(FeasibilityErrorReason.INVALID_TARGET, defend_enemy_result.reason_code)
        self.assertFalse(scout_unknown_result.executable)
        self.assertEqual(FeasibilityErrorReason.INVALID_TARGET, scout_unknown_result.reason_code)

    def test_summarize_state_is_read_only_and_feasible_for_any_valid_state(self) -> None:
        state = ToyCraftState(
            resources=ResourceState(minerals=500),
            units={"SCV": 4, "Marine": 1},
            structures={"Command Center": 1},
        )

        result = validate_intent_feasibility(SummarizeStateIntent(), state)

        self.assertTrue(result.executable)
        self.assertEqual(SummarizeStateIntent(), result.payload)

    def test_patrol_constraints_are_allowed_only_between_friendly_reachable_points(self) -> None:
        state = ready_state()

        friendly_result = validate_intent_feasibility(
            DefendIntent(
                priority="high",
                constraints=("patrol between main ramp and natural choke",),
                location="natural choke",
                unit_group="2 Marines",
            ),
            state,
        )
        enemy_patrol_result = validate_intent_feasibility(
            DefendIntent(
                priority="high",
                constraints=("patrol between main ramp and enemy front",),
                location="main ramp",
                unit_group="2 Marines",
            ),
            state,
        )

        self.assertTrue(friendly_result.executable)
        self.assertFalse(enemy_patrol_result.executable)
        self.assertEqual(FeasibilityErrorReason.INVALID_TARGET, enemy_patrol_result.reason_code)
        self.assertIn("Patrol route cannot include enemy front", enemy_patrol_result.reason)

    def test_repair_reachability_requires_a_friendly_damaged_target(self) -> None:
        state = ToyCraftState(
            resources=ResourceState(minerals=500),
            units={"SCV": 4},
            structures={"Command Center": 1},
            damaged_targets=("front bunker",),
        )

        result = validate_intent_feasibility(
            RepairIntent(target="enemy front", worker_count=1),
            state,
        )

        self.assertFalse(result.executable)
        self.assertEqual(FeasibilityErrorReason.INVALID_TARGET, result.reason_code)
        self.assertIn("not a repairable friendly target", result.reason)


class FocusedIntentFeasibilityMatrixTest(unittest.TestCase):
    def test_each_supported_intent_has_a_focused_feasible_case(self) -> None:
        cases = executable_intent_feasibility_cases()

        self.assertEqual(set(CANONICAL_INTENT_NAMES), {intent_name for intent_name, _, _ in cases})
        for intent_name, payload, state in cases:
            with self.subTest(intent=intent_name):
                result = validate_intent_feasibility(payload, state)

                self.assertTrue(result.executable)
                self.assertEqual(ValidationStatus.EXECUTABLE, result.status)
                self.assertEqual(payload, result.payload)
                self.assertEqual((), result.issues)

    def test_each_raw_intent_dsl_variant_passes_executable_validation_path(self) -> None:
        cases = executable_intent_feasibility_cases()

        self.assertEqual(set(CANONICAL_INTENT_NAMES), {intent_name for intent_name, _, _ in cases})
        for intent_name, payload, state in cases:
            with self.subTest(intent=intent_name):
                result = validate_intent_feasibility(payload.to_dict(), state)

                self.assertTrue(result.executable)
                self.assertEqual(ValidationStatus.EXECUTABLE, result.status)
                self.assertEqual(payload, result.payload)
                self.assertEqual((), result.issues)

    def test_mapped_raw_intent_dsl_rejects_current_state_feasibility_failures(self) -> None:
        for category, payload, state, expected_reason, expected_detail in raw_feasibility_blocking_cases():
            with self.subTest(category=category, intent=payload.intent):
                result = validate_intent_feasibility(payload.to_dict(), state)

                self.assertFalse(result.executable)
                self.assertEqual(ValidationStatus.REJECTED, result.status)
                self.assertEqual(payload, result.payload)
                self.assertEqual(expected_reason, result.reason_code)
                self.assertIn(expected_detail, result.reason)
                self.assertTrue(result.alternative.strip())

    def test_each_supported_intent_has_a_focused_infeasible_case(self) -> None:
        cases = (
            (
                "GATHER_RESOURCE",
                GatherResourceIntent(resource="gas", worker_count=1, base="main"),
                ToyCraftState(units={"SCV": 4}, structures={"Command Center": 1}),
                FeasibilityErrorReason.MISSING_PREREQUISITE,
            ),
            (
                "BUILD_STRUCTURE",
                BuildStructureIntent(structure="Bunker", location="natural choke"),
                ToyCraftState(
                    resources=ResourceState(minerals=500),
                    units={"SCV": 4},
                    structures={"Command Center": 1},
                ),
                FeasibilityErrorReason.MISSING_PREREQUISITE,
            ),
            (
                "TRAIN_WORKER",
                TrainWorkerIntent(count=1),
                ToyCraftState(
                    resources=ResourceState(minerals=500),
                    supply=SupplyState(used_supply=15, supply_capacity=15),
                    structures={"Command Center": 1},
                ),
                FeasibilityErrorReason.INSUFFICIENT_SUPPLY,
            ),
            (
                "TRAIN_ARMY",
                TrainArmyIntent(unit_type="Marine", count=1),
                ToyCraftState(
                    resources=ResourceState(minerals=500),
                    supply=SupplyState(used_supply=4, supply_capacity=15),
                    units={"SCV": 4},
                    structures={"Command Center": 1},
                ),
                FeasibilityErrorReason.UNAVAILABLE_PRODUCER,
            ),
            (
                "SCOUT",
                ScoutIntent(target="enemy natural", unit_group="1 SCV"),
                ToyCraftState(
                    resources=ResourceState(minerals=500),
                    units={"Marine": 2},
                    structures={"Command Center": 1},
                ),
                FeasibilityErrorReason.UNAVAILABLE_UNIT_GROUP,
            ),
            (
                "SUMMARIZE_STATE",
                SummarizeStateIntent(constraints=("attack and retreat",)),
                ready_state(),
                FeasibilityErrorReason.CONSTRAINT_CONFLICT,
            ),
            (
                "DEFEND",
                DefendIntent(location="enemy natural", unit_group="2 Marines"),
                ready_state(),
                FeasibilityErrorReason.INVALID_TARGET,
            ),
            (
                "REPAIR",
                RepairIntent(target="front bunker", worker_count=1),
                ToyCraftState(
                    resources=ResourceState(minerals=500),
                    units={"SCV": 4},
                    structures={"Command Center": 1},
                ),
                FeasibilityErrorReason.INVALID_TARGET,
            ),
            (
                "EXPAND",
                ExpandIntent(location="natural expansion"),
                ToyCraftState(
                    resources=ResourceState(minerals=1000),
                    units={"SCV": 4},
                    structures={"Command Center": 1},
                    claimed_locations=("main", "natural expansion"),
                ),
                FeasibilityErrorReason.LOCATION_UNAVAILABLE,
            ),
            (
                "HARASS",
                HarassIntent(target="main ramp", unit_group="2 Marines"),
                ready_state(),
                FeasibilityErrorReason.INVALID_TARGET,
            ),
        )

        self.assertEqual(set(CANONICAL_INTENT_NAMES), {intent_name for intent_name, _, _, _ in cases})
        for intent_name, payload, state, expected_reason in cases:
            with self.subTest(intent=intent_name):
                result = validate_intent_feasibility(payload, state)

                self.assertFalse(result.executable)
                self.assertEqual(ValidationStatus.REJECTED, result.status)
                self.assertEqual(payload, result.payload)
                self.assertEqual(expected_reason, result.reason_code)
                self.assertTrue(result.reason.strip())
                self.assertTrue(result.alternative.strip())


if __name__ == "__main__":
    unittest.main()
