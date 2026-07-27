"""Tests for exact same-update causal ProductionManager evidence."""

from __future__ import annotations

import unittest

from starcraft_commander.micromachine_production_evidence import (
    expected_production_pairs,
    find_causal_production_evidence,
)


def _production_entry(
    *,
    update_id: str = "production-update",
    doctrine: str = "bio_pressure",
    action: str = "bio_facility",
    doctrine_item: str = "Barracks",
    doctrine_frame: int = 100,
    doctrine_evidence: str = "queued",
    actual_item: str = "Barracks",
    actual_frame: int = 120,
    actual_update_id: str | None = None,
    actual_count: int = 1,
    represented_count: int = 0,
    required_count: int = 0,
    prerequisites_satisfied: bool = False,
) -> dict[str, object]:
    return {
        "frame": max(doctrine_frame, actual_frame),
        "managers": {
            "ProductionManager": {
                "bounded_intervention": True,
                "policy_update_id": update_id,
                "policy_issued_at_frame": 90,
                "strategy_doctrine": doctrine,
                "last_doctrine": doctrine,
                "last_doctrine_action": action,
                "last_doctrine_queue_item": doctrine_item,
                "last_doctrine_evidence": doctrine_evidence,
                "last_doctrine_update_id": update_id,
                "last_doctrine_frame": doctrine_frame,
                "last_doctrine_fresh": True,
                "last_doctrine_required_count": required_count,
                "last_doctrine_represented_count": represented_count,
                "last_doctrine_prerequisites_satisfied": (
                    prerequisites_satisfied
                ),
                "actual_production_command_issued_count": actual_count,
                "last_actual_production_command_kind": "build_command",
                "last_actual_production_command_item": actual_item,
                "last_actual_production_command_update_id": (
                    actual_update_id
                    if actual_update_id is not None
                    else update_id
                ),
                "last_actual_production_command_frame": actual_frame,
            }
        },
    }


class CausalProductionEvidenceTest(unittest.TestCase):
    def test_matches_exact_bio_facility_barracks_command(self) -> None:
        evidence = find_causal_production_evidence(
            (_production_entry(),),
            expected_doctrine="bio_pressure",
            expected_update_id="production-update",
            expected_pairs=expected_production_pairs(
                "bio_pressure",
                expected_actions=("bio_facility",),
                expected_items=("Barracks",),
            ),
        )

        self.assertTrue(evidence.matched)
        self.assertEqual(frozenset({"Barracks"}), evidence.expected_actual_items)

    def test_rejects_unrelated_same_update_actual_command(self) -> None:
        for actual_item in ("Marauder", "Medivac"):
            with self.subTest(actual_item=actual_item):
                evidence = find_causal_production_evidence(
                    (_production_entry(actual_item=actual_item),),
                    expected_doctrine="bio_pressure",
                    expected_update_id="production-update",
                    expected_pairs={("bio_facility", "Barracks")},
                )

                self.assertFalse(evidence.matched)
                self.assertEqual(
                    frozenset({actual_item}),
                    evidence.observed_actual_items,
                )

    def test_rejects_actual_command_before_doctrine_frame(self) -> None:
        evidence = find_causal_production_evidence(
            (
                _production_entry(
                    doctrine_frame=120,
                    actual_frame=119,
                ),
            ),
            expected_doctrine="bio_pressure",
            expected_update_id="production-update",
            expected_pairs={("bio_facility", "Barracks")},
        )

        self.assertFalse(evidence.matched)
        self.assertIn(
            "build_command|Barracks@119",
            evidence.observed_actual_commands,
        )

    def test_matches_ghost_techlab_raw_api_alias(self) -> None:
        evidence = find_causal_production_evidence(
            (
                _production_entry(
                    action="bio_ghost_techlab",
                    doctrine_item="BarracksTechLab",
                    actual_item="TERRAN_BARRACKSTECHLAB",
                ),
            ),
            expected_doctrine="bio_pressure",
            expected_update_id="production-update",
            expected_pairs={("bio_ghost_techlab", "BarracksTechLab")},
        )

        self.assertTrue(evidence.matched)
        self.assertIn("BarracksTechLab", evidence.observed_actual_items)

    def test_rejects_actual_command_from_different_update(self) -> None:
        evidence = find_causal_production_evidence(
            (
                _production_entry(
                    actual_update_id="different-update",
                ),
            ),
            expected_doctrine="bio_pressure",
            expected_update_id="production-update",
            expected_pairs={("bio_facility", "Barracks")},
        )

        self.assertFalse(evidence.matched)

    def test_rejects_incomplete_represented_satisfied_evidence(self) -> None:
        evidence = find_causal_production_evidence(
            (
                _production_entry(
                    doctrine_evidence="represented_satisfied",
                    actual_count=0,
                    actual_item="none",
                    actual_frame=0,
                    represented_count=1,
                    required_count=2,
                    prerequisites_satisfied=False,
                ),
            ),
            expected_doctrine="bio_pressure",
            expected_update_id="production-update",
            expected_pairs={("bio_facility", "Barracks")},
        )

        self.assertFalse(evidence.matched)
        self.assertIsNone(evidence.doctrine_entry)


if __name__ == "__main__":
    unittest.main()
