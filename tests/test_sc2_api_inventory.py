"""Exhaustive disposition checks for the optional python-sc2 callable surface."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from starcraft_commander.sc2_api_inventory import (
    SC2_BOT_REJECTED_RAW_METHODS,
    SC2_UNIT_ACTION_METHODS,
    SC2_UNIT_QUERY_METHODS,
    classify_python_sc2_surface,
    inspect_python_sc2_surface,
)


class SC2APISurfaceInventoryTest(unittest.TestCase):
    def test_installed_surface_has_no_unclassified_bot_callables(self) -> None:
        inventory = inspect_python_sc2_surface()
        if not inventory.package_available:
            self.skipTest(inventory.unavailable_reason)
        self.assertTrue(inventory.classification_complete, inventory.to_dict())
        self.assertEqual((), inventory.bot_unclassified)

    def test_unit_command_surface_is_fully_semantic_or_query_bounded(self) -> None:
        inventory = inspect_python_sc2_surface()
        if not inventory.package_available:
            self.skipTest(inventory.unavailable_reason)
        expected = SC2_UNIT_ACTION_METHODS | SC2_UNIT_QUERY_METHODS
        self.assertEqual(expected, set(inventory.unit_methods))
        self.assertEqual(expected, set(inventory.unit_supported))

    def test_raw_bot_orders_are_explicitly_rejected(self) -> None:
        inventory = inspect_python_sc2_surface()
        if not inventory.package_available:
            self.skipTest(inventory.unavailable_reason)
        self.assertTrue(set(SC2_BOT_REJECTED_RAW_METHODS) <= set(inventory.bot_methods))
        self.assertIn("do", inventory.bot_rejected)
        self.assertIn("synchronous_do", inventory.bot_rejected)

    def test_inventory_is_json_ready_and_explains_rejections(self) -> None:
        inventory = inspect_python_sc2_surface()
        payload = inventory.to_dict()
        self.assertIsInstance(payload["rejected_reasons"], dict)
        self.assertEqual(inventory.classification_complete, payload["classification_complete"])
        self.assertEqual(payload, json.loads(json.dumps(payload)))

    def test_unknown_unit_and_new_callback_are_not_silently_classified(self) -> None:
        class FutureUnit:
            def new_order(self):
                raise AssertionError("must not run")

        class FutureBot:
            def on_new_event(self):
                raise AssertionError("must not run")

        inventory = classify_python_sc2_surface(FutureUnit, FutureBot)
        self.assertFalse(inventory.classification_complete)
        self.assertEqual(("new_order",), inventory.unit_unclassified)
        self.assertEqual(("on_new_event",), inventory.bot_unclassified)

    def test_missing_optional_dependency_is_not_a_complete_inventory(self) -> None:
        with patch.dict("sys.modules", {"sc2.bot_ai": None}):
            inventory = inspect_python_sc2_surface()
        self.assertFalse(inventory.package_available)
        self.assertFalse(inventory.classification_complete)
        self.assertTrue(inventory.unavailable_reason)

    def test_discovery_never_invokes_descriptors(self) -> None:
        class RaisingDescriptor:
            def __get__(self, instance, owner):
                raise AssertionError("must not run")

        class Unit:
            health = RaisingDescriptor()

            @staticmethod
            def move():
                raise AssertionError("must not run")

        inventory = classify_python_sc2_surface(Unit, object)
        self.assertEqual(("move",), inventory.unit_methods)

    def test_classified_does_not_mean_all_apis_implemented(self) -> None:
        class Bot:
            def alert(self):
                pass

            def chat_send(self):
                pass

        inventory = classify_python_sc2_surface(object, Bot)
        self.assertTrue(inventory.classification_complete)
        self.assertEqual(("alert", "chat_send"), inventory.bot_unimplemented)
        self.assertNotIn("chat_send", inventory.bot_rejected)
