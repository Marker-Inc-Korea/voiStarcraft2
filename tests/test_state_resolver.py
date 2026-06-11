import unittest

import toycraft_commander as package_exports
from toycraft_commander.feasibility import ToyCraftState
from toycraft_commander.intents import (
    BuildStructureIntent,
    DefendIntent,
    ExpandIntent,
    GatherResourceIntent,
    HarassIntent,
    RepairIntent,
    ScoutIntent,
    SummarizeStateIntent,
    TrainArmyIntent,
    TrainWorkerIntent,
)
from toycraft_commander.resources import ResourceState, SupplyState
from toycraft_commander.state_resolver import (
    COMBAT_UNIT_GROUP_NAMES,
    PHASE_ZERO_RESOLVABLE_STRUCTURE_NAMES,
    IntentStateResolution,
    ResolvedStateReference,
    ResolvedUnitGroup,
    ToyCraftStateView,
    resolve_base_reference,
    resolve_intent_state_references,
    resolve_location_reference,
    resolve_phase_zero_structure_name,
    resolve_producer_reference,
    resolve_repair_target_reference,
    resolve_resource_reference,
    resolve_structure_reference,
    resolve_target_reference,
    resolve_unit_group_reference,
    resolve_unit_type_reference,
    resolve_worker_reference,
)


def resolver_state() -> ToyCraftState:
    return ToyCraftState(
        resources=ResourceState(minerals=650, gas=125),
        supply=SupplyState(used_supply=11, supply_capacity=23),
        units={"SCV": 7, "Marine": 4, "Vulture": 1},
        structures={
            "Command Center": 1,
            "Supply Depot": 1,
            "Barracks": 1,
            "Refinery": 1,
            "Bunker": 1,
        },
        busy_workers=2,
        busy_producers={"Barracks": 1},
        production_queues={"Command Center": 1, "Barracks": 2},
        claimed_locations=("main", "main base"),
        damaged_targets=("front bunker",),
        unit_positions={"Marine": "main ramp"},
    )


class StateResolverSurfaceTest(unittest.TestCase):
    def test_package_exports_state_resolver_boundary(self) -> None:
        self.assertIs(COMBAT_UNIT_GROUP_NAMES, package_exports.COMBAT_UNIT_GROUP_NAMES)
        self.assertIs(
            PHASE_ZERO_RESOLVABLE_STRUCTURE_NAMES,
            package_exports.PHASE_ZERO_RESOLVABLE_STRUCTURE_NAMES,
        )
        self.assertIs(IntentStateResolution, package_exports.IntentStateResolution)
        self.assertIs(ResolvedStateReference, package_exports.ResolvedStateReference)
        self.assertIs(ResolvedUnitGroup, package_exports.ResolvedUnitGroup)
        self.assertIs(ToyCraftStateView, package_exports.ToyCraftStateView)
        self.assertIs(resolve_base_reference, package_exports.resolve_base_reference)
        self.assertIs(
            resolve_intent_state_references,
            package_exports.resolve_intent_state_references,
        )
        self.assertIs(resolve_location_reference, package_exports.resolve_location_reference)
        self.assertIs(
            resolve_phase_zero_structure_name,
            package_exports.resolve_phase_zero_structure_name,
        )
        self.assertIs(resolve_producer_reference, package_exports.resolve_producer_reference)
        self.assertIs(
            resolve_repair_target_reference,
            package_exports.resolve_repair_target_reference,
        )
        self.assertIs(resolve_resource_reference, package_exports.resolve_resource_reference)
        self.assertIs(resolve_structure_reference, package_exports.resolve_structure_reference)
        self.assertIs(resolve_target_reference, package_exports.resolve_target_reference)
        self.assertIs(
            resolve_unit_group_reference,
            package_exports.resolve_unit_group_reference,
        )
        self.assertIs(resolve_unit_type_reference, package_exports.resolve_unit_type_reference)
        self.assertIs(resolve_worker_reference, package_exports.resolve_worker_reference)

    def test_resolution_records_are_json_ready_and_validate_shape(self) -> None:
        reference = ResolvedStateReference(
            field_name="target",
            kind="target",
            requested="상대 미네랄",
            canonical_name="enemy mineral line",
            available=True,
            metadata={"kind": "enemy_position"},
        )

        self.assertEqual(
            {
                "field_name": "target",
                "kind": "target",
                "requested": "상대 미네랄",
                "canonical_name": "enemy mineral line",
                "available": True,
                "metadata": {"kind": "enemy_position"},
            },
            reference.to_dict(),
        )
        with self.assertRaisesRegex(ValueError, "available references require canonical_name"):
            ResolvedStateReference(
                field_name="target",
                kind="target",
                requested="x",
                canonical_name=None,
                available=True,
            )
        with self.assertRaisesRegex(ValueError, "unavailable references require reason"):
            ResolvedStateReference(
                field_name="target",
                kind="target",
                requested="x",
                canonical_name=None,
                available=False,
            )


class StateResolverReferenceTest(unittest.TestCase):
    def test_resolves_resources_bases_structures_and_locations_against_state(self) -> None:
        state = resolver_state()

        resource = resolve_resource_reference("minerals", state)
        base = resolve_base_reference("본진", state)
        structure = resolve_structure_reference("벙커", state)
        producer = resolve_producer_reference("Barracks", state)
        location = resolve_location_reference("입구", state)

        self.assertTrue(resource.available)
        self.assertEqual("minerals", resource.canonical_name)
        self.assertEqual(650, resource.metadata["amount"])
        self.assertTrue(base.available)
        self.assertEqual("main base", base.canonical_name)
        self.assertTrue(base.metadata["claimed"])
        self.assertEqual("Bunker", structure.canonical_name)
        self.assertEqual(1, structure.metadata["completed_count"])
        self.assertTrue(producer.available)
        self.assertEqual(0, producer.metadata["available_count"])
        self.assertEqual(2, producer.metadata["queue_slots"])
        self.assertEqual("main ramp", location.canonical_name)
        self.assertEqual(("Marine",), location.metadata["occupied_by"])

    def test_resolves_targets_repair_targets_workers_units_and_groups(self) -> None:
        state = resolver_state()

        target = resolve_target_reference("상대 미네랄", state)
        repair_target = resolve_repair_target_reference("앞벙커", state)
        worker = resolve_worker_reference(3, state)
        unit_type = resolve_unit_type_reference("마린", state)
        marine_group = resolve_unit_group_reference("2 Marines", state)
        combat_group = resolve_unit_group_reference("available combat units", state)

        self.assertTrue(target.available)
        self.assertEqual("enemy mineral line", target.canonical_name)
        self.assertEqual("enemy_position", target.metadata["kind"])
        self.assertTrue(repair_target.available)
        self.assertEqual("front bunker", repair_target.canonical_name)
        self.assertTrue(repair_target.metadata["damaged"])
        self.assertTrue(worker.available)
        self.assertEqual("SCV", worker.canonical_name)
        self.assertEqual(5, worker.metadata["available_count"])
        self.assertTrue(unit_type.available)
        self.assertEqual("Marine", unit_type.canonical_name)
        self.assertEqual("Barracks", unit_type.metadata["producer"])
        self.assertTrue(marine_group.available)
        self.assertEqual("Marine", marine_group.unit_name)
        self.assertEqual(2, marine_group.selected_count)
        self.assertTrue(combat_group.available)
        self.assertEqual("Marine", combat_group.unit_name)
        self.assertEqual(4, combat_group.selected_count)

    def test_unavailable_references_return_reasons_without_mutating_state(self) -> None:
        state = resolver_state()
        before_state = state

        unknown_location = resolve_location_reference("섬멀티", state)
        unclaimed_base = resolve_base_reference("앞마당", state)
        undamaged_repair_target = resolve_repair_target_reference("main ramp", state)
        too_many_workers = resolve_worker_reference(8, state)
        enemy_group = resolve_unit_group_reference("1 Zealot", state)
        unknown_group = resolve_unit_group_reference("dropship squad", state)

        self.assertIs(state, before_state)
        self.assertFalse(unknown_location.available)
        self.assertIn("not a known ToyCraft location", unknown_location.reason)
        self.assertFalse(unclaimed_base.available)
        self.assertIn("not currently claimed", unclaimed_base.reason)
        self.assertFalse(undamaged_repair_target.available)
        self.assertIn("not a repair target", undamaged_repair_target.reason)
        self.assertFalse(too_many_workers.available)
        self.assertIn("only 5 are free", too_many_workers.reason)
        self.assertFalse(enemy_group.available)
        self.assertIn("not player-controlled", enemy_group.reason)
        self.assertFalse(unknown_group.available)
        self.assertIn("does not name a supported ToyCraft unit group", unknown_group.reason)


class IntentStateResolverTest(unittest.TestCase):
    def test_all_ten_intents_have_state_resolution_paths(self) -> None:
        state = resolver_state()
        payloads = (
            GatherResourceIntent(resource="minerals", worker_count=2, base="main"),
            BuildStructureIntent(structure="Bunker", location="natural choke"),
            TrainWorkerIntent(count=1),
            TrainArmyIntent(unit_type="Marine", count=1),
            ScoutIntent(target="enemy natural", unit_group="1 SCV"),
            SummarizeStateIntent(),
            DefendIntent(location="main ramp", unit_group="2 Marines"),
            RepairIntent(target="front bunker", worker_count=1),
            ExpandIntent(location="natural expansion"),
            HarassIntent(target="enemy mineral line", unit_group="2 Marines"),
        )

        for payload in payloads:
            with self.subTest(intent=payload.intent):
                resolution = resolve_intent_state_references(payload, state)

                self.assertEqual(payload.intent, resolution.intent)
                self.assertTrue(resolution.all_references_available)
                self.assertEqual({"minerals": 650, "gas": 125}, resolution.resource_snapshot)
                self.assertEqual(12, resolution.supply_snapshot["available_supply"])

    def test_resolution_exposes_unresolved_fields_and_stable_dict_shape(self) -> None:
        state = ToyCraftState(
            resources=ResourceState(minerals=500),
            units={"SCV": 4},
            structures={"Command Center": 1},
        )

        resolution = resolve_intent_state_references(
            HarassIntent(target="front bunker", unit_group="2 Marines"),
            state,
        )

        self.assertFalse(resolution.all_references_available)
        self.assertEqual(
            ("unit_group",),
            tuple(ref.field_name for ref in resolution.unresolved_references),
        )
        self.assertEqual("front bunker", resolution.get_reference("target").canonical_name)
        self.assertIs(resolution.get_reference("missing"), None)
        self.assertEqual(
            {
                "intent",
                "references",
                "unit_group",
                "resource_snapshot",
                "supply_snapshot",
                "all_references_available",
            },
            set(resolution.to_dict()),
        )

    def test_phase_zero_structure_resolver_canonicalizes_special_structures(self) -> None:
        self.assertEqual("Command Center", resolve_phase_zero_structure_name("commandcenter"))
        self.assertEqual("Bunker", resolve_phase_zero_structure_name("bunker"))
        self.assertEqual("Supply Depot", resolve_phase_zero_structure_name("서플"))
        self.assertIsNone(resolve_phase_zero_structure_name("Starport"))


if __name__ == "__main__":
    unittest.main()
