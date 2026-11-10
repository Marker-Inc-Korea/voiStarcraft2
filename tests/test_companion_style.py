"""Style guards for the companion page (docs/design-direction.md)."""

from __future__ import annotations

import re
import unittest

from starcraft_commander.companion_ui import render_companion_page


class CompanionStyleGuardTest(unittest.TestCase):
    def setUp(self) -> None:
        page = render_companion_page()
        self.css = page[page.index("<style>"):page.index("</style>")]

    def test_no_gradients_glows_or_uppercase_labels(self) -> None:
        self.assertNotIn("gradient(", self.css)
        self.assertNotIn("text-transform", self.css)
        self.assertNotIn("backdrop-filter", self.css)
        for shadow in re.findall(r"box-shadow:[^;]+;", self.css):
            self.fail(f"companion chrome must not use box-shadow: {shadow}")

    def test_weights_stay_within_400_to_600(self) -> None:
        weights = {int(w) for w in re.findall(r"font-weight:\s*(\d+)", self.css)}
        self.assertTrue(weights)
        self.assertLessEqual(max(weights), 600)

    def test_only_the_live_recording_state_animates(self) -> None:
        animated = re.findall(r"([^{}]+)\{[^{}]*\banimation:", self.css)
        self.assertEqual([a.strip() for a in animated], [".icon-button.recording"])


if __name__ == "__main__":
    unittest.main()
