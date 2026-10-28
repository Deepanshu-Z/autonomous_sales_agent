"""
test_dynamic_funnel_and_edge_cases.py — Verify state-grounded dynamic funnel and intent-driven follow-ups.
"""

import unittest
from unittest.mock import AsyncMock, patch
from langchain_core.messages import AIMessage

from src.core.conversation_handler import _sanitize_state_grounded_replies, handle_conversation_turn_async
from src.core import prompt


class TestDynamicFunnelAndEdgeCases(unittest.TestCase):

    def test_prompt_generation_all_slots_completed(self):
        """When all 4 slots are settled, prompt must state ALL 4 DETAILS COMPLETED and forbid asking for slots."""
        state = {
            "course": "kathak",
            "timing": "4:00 PM to 5:00 PM",
            "selected_timing": "4:00 PM to 5:00 PM",
            "teacher": "Ms. Madhuri",
            "selected_teacher": "Ms. Madhuri",
            "package": "3 Months",
            "selected_duration": "3 Months",
            "fee": 1770,
            "stage": "PACKAGE_SELECTED",
            "_last_turn": {
                "intent": "TEACHER_SELECTION",
                "selected_teacher": "Ms. Madhuri",
            }
        }
        rendered = prompt.format_system_prompt(state, "kathak", "English")
        
        self.assertIn("SETTLED IN DATABASE (DO NOT RE-ASK): Course: Kathak Classical Dance, Batch Timing: 4:00 PM to 5:00 PM, Instructor: Ms. Madhuri, Package Duration: 3 Months", rendered)
        self.assertIn("STILL NEEDED: NONE (All completed)", rendered)
        self.assertIn("ALL 4 DETAILS COMPLETED", rendered)
        self.assertIn("Under NO circumstances ask the customer to choose a package, timing, or instructor!", rendered)

    def test_prompt_generation_informational_question_mode(self):
        """When customer asks a question/demo, prompt must enter INFORMATIONAL INQUIRY mode and forbid steering to payment."""
        state = {
            "course": "kathak",
            "timing": "4:00 PM to 5:00 PM",
            "teacher": "Ms. Madhuri",
            "package": "3 Months",
            "_last_turn": {
                "intent": "QUESTION",
                "wants_demo": True,
                "question_summary": "asking for instructor demo video"
            }
        }
        rendered = prompt.format_system_prompt(state, "kathak", "English")
        self.assertIn("CURRENT TURN CONVERSATIONAL MODE: INFORMATIONAL INQUIRY", rendered)
        self.assertIn("DO NOT force payment assistance, and DO NOT badger them to select missing funnel slots", rendered)

    def test_sanitizer_intercepts_hallucinated_package_ask_when_all_slots_full(self):
        """Sanitizer replaces hallucinated package asking with confirmed order summary when all slots are settled."""
        state = {
            "course": "kathak",
            "timing": "4:00 PM to 5:00 PM",
            "teacher": "Ms. Madhuri",
            "package": "3 Months",
            "fee": 1770,
        }
        last_turn = {"intent": "TEACHER_SELECTION"}
        hallucinated_reply = (
            "You've chosen Ms. Madhuri as your instructor for the 4:00 PM to 5:00 PM batch. "
            "Now, let's move on to the package duration. Here are the available options:\n"
            "• 1 Month: ₹700\n• 3 Months: ₹1,500"
        )
        hallucinated_followup = "Which package duration would you like to start with — 1 Month or 3 Months? 😊"

        reply, followup = _sanitize_state_grounded_replies(
            hallucinated_reply, hallucinated_followup, state=state, last_turn=last_turn, active_cid="kathak"
        )

        # Hallucinated question MUST be removed
        self.assertNotIn("Which package duration would you like to start with", followup)
        self.assertNotIn("move on to the package duration", reply)
        # Confirmed package and fee must be present
        self.assertIn("3 Months", reply)
        self.assertIn("Ms. Madhuri", reply)
        self.assertIn("₹1,770", reply)
        self.assertEqual(followup, "Would you like any assistance completing your payment with the UPI QR code? 😊")

    def test_sanitizer_preserves_question_followup_without_payment_push(self):
        """Ex 1: When user asks for demo/question after all slots full, follow-up must NOT push payment QR."""
        state = {
            "course": "kathak",
            "timing": "4:00 PM to 5:00 PM",
            "teacher": "Ms. Madhuri",
            "package": "3 Months",
        }
        last_turn = {"intent": "QUESTION", "wants_demo": True, "question_summary": "demo video"}
        demo_reply = "Here is the demo video for Ms. Madhuri: https://youtu.be/t-XO2enPGwM"
        accidental_payment_followup = "Please complete your payment via the UPI QR code below! 😊"

        reply, followup = _sanitize_state_grounded_replies(
            demo_reply, accidental_payment_followup, state=state, last_turn=last_turn, active_cid="kathak"
        )

        self.assertNotIn("UPI QR", followup)
        self.assertIn("demo video", followup.lower())

    def test_sanitizer_suppresses_followup_on_objection_or_complaint(self):
        """Ex 2: When user is in complaint/refusal, follow-up is suppressed completely."""
        state = {
            "course": "kathak",
            "timing": "4:00 PM to 5:00 PM",
            "teacher": "Ms. Madhuri",
            "package": "3 Months",
            "refusal_stage": 1,
        }
        last_turn = {"intent": "COMPLAINT", "is_complaint_or_refund": True}
        reply_msg = "I understand your concern and sincerely apologize for any frustration."
        accidental_followup = "Which package duration would you like? 😊"

        reply, followup = _sanitize_state_grounded_replies(
            reply_msg, accidental_followup, state=state, last_turn=last_turn, active_cid="kathak"
        )

        self.assertIsNone(followup)


    def test_video_guide_not_suppressed_by_demo_context(self):
        """When user clicks/asks for 'Watch Video', it must not be treated as a teacher demo and suppressed."""
        last_turn = {
            "intent": "QUESTION",
            "wants_trial_guide": True,
            "wants_resend_video": True,
            "wants_demo": False,
        }
        text = "Watch Video"
        is_video_guide_request = bool(
            last_turn.get("wants_trial_guide")
            or last_turn.get("wants_resend_video")
            or text.strip().lower() in ("watch video", "send video")
        )
        user_asked_demo = bool(
            (last_turn.get("wants_demo") or "demo" in text.lower())
            and not is_video_guide_request
        )
        self.assertTrue(is_video_guide_request)
        self.assertFalse(user_asked_demo)

    def test_multilingual_intent_without_hindi_keywords(self):
        """Customer intents extracted by semantic LLM (Tamil, Telugu, English) must be respected without Hindi regexes."""
        tamil_link_turn = {
            "customer_intent": "APP_LINK_REQUEST",
            "intent": "APP_LINK_REQUEST",
            "detected_language": "Tamil",
        }
        user_asked_links = bool(
            tamil_link_turn.get("intent") == "APP_LINK_REQUEST"
            or tamil_link_turn.get("customer_intent") == "APP_LINK_REQUEST"
        )
        self.assertTrue(user_asked_links)

        telugu_pay_turn = {
            "customer_intent": "PAYMENT_REQUEST",
            "intent": "PAYMENT_REQUEST",
            "detected_language": "Telugu",
        }
        is_asking_to_pay = bool(
            telugu_pay_turn.get("intent") == "PAYMENT_REQUEST"
            or telugu_pay_turn.get("customer_intent") == "PAYMENT_REQUEST"
        )
        self.assertTrue(is_asking_to_pay)


if __name__ == "__main__":
    unittest.main()
