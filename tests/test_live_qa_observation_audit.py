import unittest

from starcraft_commander.live_qa_observation_audit import ObservationAudit


class RawObservationTests(unittest.TestCase):
    def observe(self, audit, frame, units=(), actions=None, **values):
        audit.observe({
            "schema_version": 1, "source": "sc2_observation", "frame": frame,
            "units": list(units), **values,
        }, frame, actions or {})

    def test_refund_is_not_income(self):
        audit = ObservationAudit()
        self.observe(audit, 1, minerals=0, collected_minerals=100)
        self.observe(audit, 2, minerals=500, collected_minerals=100)
        self.assertNotIn("positive_mineral_income", audit.evidence)
        self.observe(audit, 3, collected_minerals=105)
        self.assertEqual(3, audit.evidence["positive_mineral_income"])

    def test_existing_completed_building_is_not_new_construction(self):
        audit = ObservationAudit()
        depot = {"tag": 1, "alliance": 1, "type": "SupplyDepot", "build_progress": 1}
        self.observe(audit, 1, [depot])
        self.observe(audit, 2, [depot], {"build_command|SupplyDepot": 2})
        self.assertEqual({}, audit.production_evidence("04-supply-recovery"))

    def test_new_building_requires_same_tag_to_finish(self):
        audit = ObservationAudit()
        actions = {"build_command|SupplyDepot": 2}
        self.observe(audit, 1)
        depot = {"tag": 1, "alliance": 1, "type": "SupplyDepot", "build_progress": 0.2}
        self.observe(audit, 3, [depot], actions)
        self.observe(audit, 4, [{**depot, "tag": 2, "build_progress": 1}], actions)
        self.assertNotIn("building_completed", audit.production_evidence("04-supply-recovery"))
        self.observe(audit, 5, [{**depot, "build_progress": 1}], actions)
        self.assertEqual(5, audit.production_evidence("04-supply-recovery")["building_completed"])

    def test_worker_assignment_requires_observed_harvest_order(self):
        audit = ObservationAudit()
        worker = {
            "tag": 1, "alliance": 1, "type": "SCV", "build_progress": 1,
            "orders": [{"ability_name": "MOVE", "target_tag": 2}],
        }
        mineral = {"tag": 2, "alliance": 3, "mineral_contents": 500}
        self.observe(audit, 1, [worker, mineral])
        self.assertNotIn("worker_assignment_observed", audit.evidence)
        worker["orders"][0]["ability_name"] = "HARVEST_GATHER_SCV"
        self.observe(audit, 2, [worker, mineral])
        self.assertEqual(2, audit.evidence["worker_assignment_observed"])

    def expansion_rows(self, audit, *, scoped=True, completed=True):
        actions = {"build_command|CommandCenter": 2} if scoped else {}
        self.observe(audit, 1)
        cc = {
            "tag": 10, "alliance": 1, "type": "TERRAN_COMMANDCENTER",
            "build_progress": 0.2,
        }
        self.observe(audit, 3, [cc], actions)
        cc = {**cc, "build_progress": 1 if completed else 0.5}
        self.observe(audit, 4, [cc], actions)
        return cc, actions

    @staticmethod
    def marine_barracks():
        return {
            "tag": 20, "alliance": 1, "type": "TERRAN_BARRACKS",
            "build_progress": 1, "orders": [{"ability_name": "TRAIN_MARINE"}],
        }

    def test_expansion_continuity_requires_later_order_and_new_marine(self):
        audit = ObservationAudit()
        cc, actions = self.expansion_rows(audit)
        barracks = self.marine_barracks()
        self.observe(audit, 5, [cc, barracks], actions)
        self.assertEqual(
            {"building_completed": 4},
            audit.production_evidence("19-expand-production-resume"),
        )
        marine = {
            "tag": 30, "alliance": 1, "type": "TERRAN_MARINE",
            "build_progress": 1,
        }
        self.observe(audit, 6, [cc, barracks, marine], actions)
        self.assertEqual(
            {"building_completed": 4, "train_command|Marine": 5},
            audit.production_evidence("19-expand-production-resume"),
        )
        self.assertEqual(30, audit.expansion_resume["produced_tag"])

    def test_unscoped_expansion_or_unfinished_building_cannot_prove_resume(self):
        for options in ({"scoped": False}, {"completed": False}):
            with self.subTest(options=options):
                audit = ObservationAudit()
                cc, actions = self.expansion_rows(audit, **options)
                self.observe(audit, 5, [cc, self.marine_barracks()], actions)
                self.assertEqual(
                    {}, audit.production_evidence("19-expand-production-resume")
                )

    def test_marine_present_at_first_post_expansion_order_is_not_new(self):
        audit = ObservationAudit()
        cc, actions = self.expansion_rows(audit)
        marine = {
            "tag": 30, "alliance": 1, "type": "TERRAN_MARINE",
            "build_progress": 1,
        }
        units = [cc, self.marine_barracks(), marine]
        self.observe(audit, 5, units, actions)
        self.observe(audit, 6, units, actions)
        self.assertNotIn(
            "train_command|Marine",
            audit.production_evidence("19-expand-production-resume"),
        )

    def test_order_before_expansion_completion_cannot_prove_resume(self):
        audit = ObservationAudit()
        cc, actions = self.expansion_rows(audit, completed=False)
        self.observe(audit, 5, [cc, self.marine_barracks()], actions)
        self.observe(audit, 6, [{**cc, "build_progress": 1}], actions)
        self.assertEqual(
            {"building_completed": 6},
            audit.production_evidence("19-expand-production-resume"),
        )

    def test_zero_progress_scoped_expansion_keeps_command_identity(self):
        audit = ObservationAudit()
        actions = {"build_command|CommandCenter": 2}
        cc = {
            "tag": 10, "alliance": 1, "type": "TERRAN_COMMANDCENTER",
            "build_progress": 0,
        }
        self.observe(audit, 1)
        self.observe(audit, 3, [cc], actions)
        self.observe(audit, 4, [{**cc, "build_progress": 0.5}], actions)
        self.observe(audit, 5, [{**cc, "build_progress": 1}], actions)
        self.assertEqual(2, audit.building[10]["command_frame"])
        self.assertEqual(5, audit.expansion_resume["building_completed"])

    def supply_row(self, audit, frame, used, cap, units=(), provider=None):
        audit.observe({
            "schema_version": 1, "source": "sc2_observation", "frame": frame,
            "units": list(units),
        }, frame, {"build_command|SupplyDepot": 2}, supply={
            "current_supply": used, "max_supply": cap,
        }, supply_provider_frame=provider)

    def test_supply_recovery_requires_depot_capacity_order_and_new_unit(self):
        audit = ObservationAudit()
        depot = {
            "tag": 10, "alliance": 1, "type": "SupplyDepot", "build_progress": 0,
        }
        self.supply_row(audit, 1, 15, 15)
        self.supply_row(audit, 2, 15, 15, [depot], provider=2)
        depot["build_progress"] = 1
        self.supply_row(audit, 3, 15, 23, [depot], provider=2)
        barracks = self.marine_barracks()
        self.supply_row(audit, 4, 15, 23, [depot, barracks], provider=2)
        self.assertNotIn("production_resumed", audit.supply_recovery)
        marine = {"tag": 30, "alliance": 1, "type": "Marine", "build_progress": 1}
        self.supply_row(audit, 5, 16, 23, [depot, barracks, marine], provider=2)
        self.assertEqual({
            "supply_blocked": 1, "supply_provider_command": 2, "production_resumed": 5,
        }, audit.production_evidence("28-supply-block-recovery"))

    def test_supply_losses_and_unscoped_commands_cannot_prove_recovery(self):
        for provider, recovered_cap in ((None, 23), (0, 23), (2, 15)):
            with self.subTest(provider=provider, recovered_cap=recovered_cap):
                audit = ObservationAudit()
                depot = {
                    "tag": 10, "alliance": 1, "type": "SupplyDepot",
                    "build_progress": 0.5,
                }
                self.supply_row(audit, 1, 15, 15)
                self.supply_row(audit, 2, 15, 15, [depot], provider)
                depot["build_progress"] = 1
                self.supply_row(audit, 3, 10, recovered_cap, [depot], provider)
                self.supply_row(audit, 4, 10, recovered_cap, [
                    depot, self.marine_barracks(),
                ], provider)
                self.supply_row(audit, 5, 11, recovered_cap, [
                    depot, {"tag": 30, "alliance": 1, "type": "Marine",
                            "build_progress": 1},
                ], provider)
                self.assertNotIn("production_resumed", audit.supply_recovery)

    def test_stale_observation_is_ignored(self):
        audit = ObservationAudit()
        audit.observe({
            "schema_version": 1, "source": "sc2_observation", "frame": 2,
            "units": [],
        }, 3, {})
        self.assertEqual(0, audit.samples)

    def test_scoped_live_order_proves_actual_sc2_action(self):
        audit = ObservationAudit()
        tank = {
            "tag": 7, "alliance": 1, "type": "SiegeTank",
            "build_progress": 1,
            "orders": [{"ability_name": "MORPH_SIEGEMODE"}],
        }
        self.observe(audit, 10, [tank])
        self.assertNotIn("actual_sc2_action", audit.evidence)
        self.observe(audit, 20, [tank], {})
        # Scope is supplied by the operation audit, not inferred from the
        # mere presence of a tank in raw SC2 observations.
        audit.observe({
            "schema_version": 1, "source": "sc2_observation", "frame": 30,
            "units": [tank],
        }, 30, {}, scoped_unit_tags={7})
        self.assertEqual(30, audit.evidence["actual_sc2_action"])

    def test_scoped_unit_position_change_proves_movement(self):
        audit = ObservationAudit()
        tank = {
            "tag": 7, "alliance": 1, "type": "SiegeTank",
            "build_progress": 1, "x": 10, "y": 10, "orders": [],
        }
        audit.observe({
            "schema_version": 1, "source": "sc2_observation", "frame": 10,
            "units": [tank],
        }, 10, {}, scoped_unit_tags={7})
        moved = {**tank, "x": 12, "y": 10}
        audit.observe({
            "schema_version": 1, "source": "sc2_observation", "frame": 20,
            "units": [moved],
        }, 20, {}, scoped_unit_tags={7})
        self.assertEqual(20, audit.evidence["scoped_movement_observed"])


if __name__ == "__main__":
    unittest.main()
