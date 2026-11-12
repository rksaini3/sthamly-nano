"""Desk prompts: sahi button ko sahi prompt mile, aur profile ke bahar ka action na chale."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "desk"))

import desk_prompts as p  # noqa: E402


class PromptMapping(unittest.TestCase):
    def test_notice_label_gets_notice_prompt(self):
        self.assertIn("legal notice", p.action_prompt("Notice ka draft"))
        self.assertIn("legal notice", p.action_prompt("draft_notice"))

    def test_bail_label_gets_bail_prompt_not_notice(self):
        prompt = p.action_prompt("Bail application ka draft")
        self.assertIn("bail application", prompt)
        self.assertNotIn("legal notice", prompt)
        self.assertIn("bail application", p.action_prompt("draft_bail"))

    def test_fuzzy_or_unknown_input_gets_nothing(self):
        self.assertIsNone(p.action_prompt("draft"))
        self.assertIsNone(p.action_prompt("kuch bhi"))
        self.assertIsNone(p.action_prompt(""))


class Permissions(unittest.TestCase):
    def test_lawyer_gets_its_three_actions(self):
        for action in ("draft_notice", "draft_bail", "extract_ratios"):
            self.assertTrue(p.allowed("lawyer", action))

    def test_other_actions_refused(self):
        self.assertFalse(p.allowed("lawyer", "unknown_action"))
        self.assertFalse(p.allowed("general", "draft_notice"))

    def test_buttons_start_with_upload(self):
        labels = p.button_labels("lawyer")
        self.assertEqual(labels[0], p.UPLOAD_LABEL)
        self.assertEqual(len(labels), 4)
        self.assertEqual(p.button_labels("general"), [])


if __name__ == "__main__":
    unittest.main()
