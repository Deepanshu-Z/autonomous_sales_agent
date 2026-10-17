"""
test_payment_claim_no_followup.py — Tests verifying NO follow up occurs when a user claims payment.
"""

import os
import sys
import unittest
from unittest.mock import AsyncMock, patch
from langchain_core.messages import AIMessage

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from src.core.conversation_handler import handle_incoming_message


class TestPaymentClaimNoFollowup(unittest.IsolatedAsyncioTestCase):

    async def test_handle_incoming_message_suppresses_followup(self):
        """handle_incoming_message must return followup=None and buttons=[] when payment claimed."""
        fake_llm_json = (
            '{"reply": "Please note that official payments reflect under your account automatically. '
            'For manual UPI verification, please type *agent* so a support coordinator can inspect.", '
            '"followup": "Would you like to enroll in 1 Month or 3 Months?", '
            '"buttons": ["1 Month", "3 Months"]}'
        )

        with patch("src.core.conversation_handler._get_reply_llm") as mock_llm_fn, \
             patch("src.core.intent_classifier.extract_intent_and_state_changes") as mock_extract:

            mock_llm = AsyncMock()
            mock_llm.ainvoke.return_value = AIMessage(content=fake_llm_json)
            mock_llm_fn.return_value = mock_llm

            mock_extract.return_value = {
                "claims_payment_or_enrollment": True,
                "customer_intent": "PAYMENT_CLAIM",
                "detected_language": "English",
                "app_installed_stated": "unmentioned",
                "profile_created_stated": "unmentioned",
                "selected_timing": None,
                "selected_package": None,
                "selected_course": None,
                "wants_trial": False,
                "requests_human_agent": False,
                "is_coupon_requested": False,
                "is_disinterested": False,
                "is_complaint_or_refund": False,
                "question_summary": None,
                "wants_resend_video": False,
            }

            state = {"course": "yoga", "stage": "NEW", "language": "English"}
            res = await handle_incoming_message(
                user_message="I have already paid the payment",
                session_state=state,
                conversation_history=[],
                course_id="yoga",
            )

            self.assertIsNone(res.get("followup"), "Followup message was not suppressed!")
            self.assertEqual(res.get("buttons"), [], "Buttons were not suppressed!")
            self.assertTrue(res["new_state"].get("payment_claimed"))


if __name__ == "__main__":
    unittest.main()
