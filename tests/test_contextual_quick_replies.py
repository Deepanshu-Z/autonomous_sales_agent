"""
test_contextual_quick_replies.py — Automated Unit Tests for Subtask 4.2 Dynamic WhatsApp Quick-Reply Buttons.

Verifies:
1. Missing TIMING slot -> displays timing periods/slots + "Talk to Agent" (never non-existent periods).
2. Missing TEACHER slot -> displays assigned instructor names from COURSE_DATA + "Talk to Agent".
3. Missing DURATION slot -> displays package duration tiers from COURSE_DATA + "Talk to Agent".
4. PAYMENT SUMMARY / CHECKOUT -> displays checkout actions + "Talk to Agent".
5. WhatsApp constraints:
   - Max 3 buttons total.
   - Max 20 characters per button title.
   - Dedicated "Talk to Agent" button always appended as the final button.
   - Correct localized agent button: English ("Talk to Agent"), Hindi ("एजेंट से बात"), Hinglish ("Agent se baat").
6. Suppression:
   - If followup is None/empty -> returns [] (zero buttons).
   - If payment claimed or escalated -> returns [] (zero buttons).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from src.core.conversation_handler import adapt_contextual_quick_replies


class TestContextualQuickReplies(unittest.TestCase):

    def test_missing_timing_slot(self):
        """When timing is not selected, buttons must be timing periods/options + agent button."""
        state = {
            "course": "kathak",
            "timing": None,
            "teacher": None,
            "package": None,
            "language": "English",
        }
        buttons = adapt_contextual_quick_replies(
            state=state,
            course_id="kathak",
            raw_buttons=None,
            followup="Which batch timing works best for you? 😊",
        )
        self.assertLessEqual(len(buttons), 3)
        self.assertEqual(buttons[-1], "Talk to Agent")
        # For Kathak, Morning batches do not exist, so no button should mention morning
        for b in buttons:
            self.assertNotIn("morning", b.lower())
            self.assertLessEqual(len(b), 20)
        self.assertTrue(any("evening" in b.lower() or "timings" in b.lower() for b in buttons[:-1]))

    def test_missing_teacher_slot_kathak(self):
        """When timing is selected but teacher is missing, buttons must display instructor names."""
        state = {
            "course": "kathak",
            "timing": "5:00 PM to 6:00 PM",
            "teacher": None,
            "package": None,
            "language": "English",
        }
        buttons = adapt_contextual_quick_replies(
            state=state,
            course_id="kathak",
            raw_buttons=None,
            followup="Which teacher would you prefer to learn with? 😊",
        )
        self.assertLessEqual(len(buttons), 3)
        self.assertEqual(buttons[-1], "Talk to Agent")
        # For Kathak 5-6 PM, teachers include Ms. Madhuri, Vibha Mittal, Sneha Maurya
        contextual = buttons[:-1]
        self.assertGreaterEqual(len(contextual), 1)
        for b in contextual:
            self.assertLessEqual(len(b), 20)
        # Check that instructor names are present
        known_teachers = ["Ms. Madhuri", "Vibha Mittal", "Sneha Maurya", "Kratika Gontiya"]
        self.assertTrue(any(t in contextual for t in known_teachers))

    def test_missing_teacher_slot_yoga(self):
        """When timing is selected in Yoga, teacher buttons reflect Yoga teachers."""
        state = {
            "course": "yoga",
            "timing": "7:00 AM to 8:00 AM",
            "teacher": None,
            "package": None,
            "language": "English",
        }
        buttons = adapt_contextual_quick_replies(
            state=state,
            course_id="yoga",
            raw_buttons=None,
            followup="Which instructor would you prefer? 😊",
        )
        self.assertLessEqual(len(buttons), 3)
        self.assertEqual(buttons[-1], "Talk to Agent")
        for b in buttons:
            self.assertLessEqual(len(b), 20)

    def test_missing_duration_slot(self):
        """When timing and teacher are selected, buttons must display package durations."""
        state = {
            "course": "kathak",
            "timing": "5:00 PM to 6:00 PM",
            "teacher": "Ms. Madhuri",
            "package": None,
            "language": "English",
        }
        buttons = adapt_contextual_quick_replies(
            state=state,
            course_id="kathak",
            raw_buttons=None,
            followup="Which package duration would you like to start with — 1 Month or 3 Months? 😊",
        )
        self.assertLessEqual(len(buttons), 3)
        self.assertEqual(buttons[-1], "Talk to Agent")
        contextual = buttons[:-1]
        self.assertIn("1 Month Pack", contextual)
        self.assertIn("3 Months Pack", contextual)
        for b in buttons:
            self.assertLessEqual(len(b), 20)

    def test_payment_summary_checkout_buttons(self):
        """When timing, teacher, and package are all selected, buttons must offer checkout actions."""
        state = {
            "course": "kathak",
            "timing": "5:00 PM to 6:00 PM",
            "teacher": "Ms. Madhuri",
            "package": "1 Month",
            "language": "English",
        }
        buttons = adapt_contextual_quick_replies(
            state=state,
            course_id="kathak",
            raw_buttons=None,
            followup="Would you like any assistance completing your checkout on the app? 😊",
        )
        self.assertLessEqual(len(buttons), 3)
        self.assertEqual(buttons[-1], "Talk to Agent")
        contextual = buttons[:-1]
        self.assertTrue(any("pay" in b.lower() or "app" in b.lower() for b in contextual))
        for b in buttons:
            self.assertLessEqual(len(b), 20)

    def test_multilingual_agent_button(self):
        """Agent button must match customer language (Hindi, Hinglish, English)."""
        state_hi = {"course": "yoga", "timing": None, "language": "Hindi"}
        btn_hi = adapt_contextual_quick_replies(
            state=state_hi,
            course_id="yoga",
            language="Hindi",
            followup="कौन सा समय आपके लिए सही रहेगा? 😊",
        )
        self.assertEqual(btn_hi[-1], "एजेंट से बात")

        state_hinglish = {"course": "yoga", "timing": None, "language": "Hinglish"}
        btn_hinglish = adapt_contextual_quick_replies(
            state=state_hinglish,
            course_id="yoga",
            language="Hinglish",
            followup="Kaun sa timing best rahega? 😊",
        )
        self.assertEqual(btn_hinglish[-1], "Agent se baat")

        state_en = {"course": "yoga", "timing": None, "language": "English"}
        btn_en = adapt_contextual_quick_replies(
            state=state_en,
            course_id="yoga",
            language="English",
            followup="Which timing works best? 😊",
        )
        self.assertEqual(btn_en[-1], "Talk to Agent")

    def test_suppression_on_payment_claim(self):
        """When payment is claimed, zero buttons must be returned."""
        state = {
            "course": "kathak",
            "payment_claimed": True,
            "timing": "5:00 PM",
            "teacher": "Ms. Madhuri",
        }
        buttons = adapt_contextual_quick_replies(
            state=state,
            course_id="kathak",
            followup="Any question?",
        )
        self.assertEqual(buttons, [])

    def test_suppression_when_no_followup(self):
        """When followup is None or empty string, zero buttons must be returned."""
        state = {"course": "kathak", "timing": None}
        buttons_none = adapt_contextual_quick_replies(
            state=state,
            course_id="kathak",
            followup=None,
        )
        self.assertEqual(buttons_none, [])

        buttons_empty = adapt_contextual_quick_replies(
            state=state,
            course_id="kathak",
            followup="   ",
        )
        self.assertEqual(buttons_empty, [])

    def test_character_limit_truncation(self):
        """All button titles must be truncated to <= 20 characters."""
        state = {"course": "kathak", "timing": "5:00 PM", "teacher": None}
        long_raw_buttons = [
            "Super Long Instructor Name That Exceeds Limit",
            "Another Very Very Long Button Label Here",
        ]
        buttons = adapt_contextual_quick_replies(
            state=state,
            course_id="kathak",
            raw_buttons=long_raw_buttons,
            followup="Select instructor: 😊",
        )
        self.assertLessEqual(len(buttons), 3)
        for b in buttons:
            self.assertLessEqual(len(b), 20)


if __name__ == "__main__":
    unittest.main()
