import copy
import unittest

from starcraft_commander.live_qa_audit import concurrent_ownership


class ConcurrentOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.expected = {("attack", 1), ("defense", 1)}
        self.overview = {
            "ownership_integrity": "valid",
            "operation_ownership": [
                {
                    "operation_id": name,
                    "generation": 1,
                    "identity": {
                        "update_id": "command", "operation_id": name,
                        "generation": 1, "game_frame": 120,
                    },
                    "operation_ownership": {
                        "owner_count": 1, "owner_tags": [tag],
                        "integrity_status": "valid",
                    },
                }
                for name, tag in (("attack", 7), ("defense", 8))
            ],
        }

    def audit(self):
        return concurrent_ownership(self.overview, self.expected, "command", 120)

    def test_disjoint_concurrent_owners_need_no_movement_effect(self):
        self.assertEqual({"attack": [7], "defense": [8]}, self.audit())

    def test_shared_tag_is_rejected(self):
        self.overview["operation_ownership"][1]["operation_ownership"]["owner_tags"] = [7]
        self.assertEqual({}, self.audit())

    def test_nonconcurrent_snapshot_is_rejected(self):
        self.overview["operation_ownership"][1]["identity"]["game_frame"] = 119
        self.assertEqual({}, self.audit())

    def test_other_update_is_rejected(self):
        self.overview["operation_ownership"][1]["identity"]["update_id"] = "other"
        self.assertEqual({}, self.audit())

    def test_other_generation_is_rejected(self):
        self.overview["operation_ownership"][1]["generation"] = 2
        self.assertEqual({}, self.audit())

    def test_duplicate_operation_is_rejected(self):
        self.overview["operation_ownership"].append(
            copy.deepcopy(self.overview["operation_ownership"][0])
        )
        self.assertEqual({}, self.audit())

    def test_invalid_integrity_is_rejected(self):
        self.overview["ownership_integrity"] = "invalid"
        self.assertEqual({}, self.audit())

    def test_single_operation_is_not_parallel_execution(self):
        self.overview["operation_ownership"].pop()
        self.assertEqual({}, self.audit())
