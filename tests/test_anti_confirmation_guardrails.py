"""
test_anti_confirmation_guardrails.py — Automated Test Suite for Strict Non-Confirmation Guardrails.

Verifies:
1. AI NEVER confirms any type of enrollment or admission.
2. AI NEVER confirms free trial class bookings.
3. AI NEVER confirms payments, bank receipts, or screenshot verifications.
4. Clean handling across English, Hinglish, and Devanagari Hindi.
5. Zero false positives on legitimate instructions (e.g. "Tap Confirm Trial Enrollment").
6. End-to-end integration via handle_incoming_message.
"""

import sys
import os
import unittest
import asyncio
from unittest.mock import AsyncMock, patch
from langchain_core.messages import AIMessage

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from src.core.conversation_handler import (
    _sanitize_confirmation_guardrails,
    handle_incoming_message,
)


class TestAntiConfirmationGuardrails(unittest.IsolatedAsyncioTestCase):

    def test_payment_confirmation_sanitization(self):
        """AI must strictly redact and replace any payment confirmation hallucination."""
        # Hinglish
        raw_hinglish = "Haan, aapka payment receive ho gaya hai aur verify ho chuka hai!"
        clean, followup = _sanitize_confirmation_guardrails(raw_hinglish, language="Hinglish (Latin script)")
        self.assertNotIn("receive ho gaya", clean)
        self.assertNotIn("verify ho chuka", clean)
        self.assertIn("AI assistant", clean)
        self.assertIn("accounts team", clean)
        self.assertIn("*agent*", clean)
        self.assertIsNone(followup)

        # English
        raw_en = "We have received your payment and your transaction is successful!"
        clean_en, followup_en = _sanitize_confirmation_guardrails(raw_en, language="English")
        self.assertNotIn("received your payment", clean_en)
        self.assertNotIn("transaction is successful", clean_en)
        self.assertIn("do not have access to bank accounts", clean_en)
        self.assertIn("*agent*", clean_en)
        self.assertIsNone(followup_en)

        # Devanagari
        raw_hi = "आपका पेमेंट मिल गया है और फीस वेरिफाई हो गई है।"
        clean_hi, followup_hi = _sanitize_confirmation_guardrails(raw_hi, language="Hindi (Devanagari script)")
        self.assertNotIn("पेमेंट मिल गया", clean_hi)
        self.assertNotIn("वेरिफाई हो गई", clean_hi)
        self.assertIn("अकाउंट्स टीम", clean_hi)
        self.assertIn("*agent*", clean_hi)
        self.assertIsNone(followup_hi)

    def test_trial_confirmation_sanitization(self):
        """AI must strictly redact and replace any trial booking confirmation."""
        # Hinglish
        raw_hinglish = "Badhai ho! Aapka trial confirm ho gaya hai kal ke liye."
        clean, followup = _sanitize_confirmation_guardrails(raw_hinglish, language="Hinglish (Latin script)")
        self.assertNotIn("trial confirm ho gaya", clean)
        self.assertIn("Sensationz App", clean)
        self.assertIn("Confirm Trial Enrollment", clean)
        self.assertIsNotNone(followup)

        # English
        raw_en = "Your free trial is confirmed for tomorrow morning at 7:00 AM!"
        clean_en, followup_en = _sanitize_confirmation_guardrails(raw_en, language="English")
        self.assertNotIn("trial is confirmed", clean_en)
        self.assertIn("Sensationz App", clean_en)
        self.assertIn("Confirm Trial Enrollment", clean_en)
        self.assertIsNotNone(followup_en)

        # Devanagari
        raw_hi = "आपकी ट्रायल क्लास बुक हो गई है कल सुबह 7 बजे के लिए।"
        clean_hi, followup_hi = _sanitize_confirmation_guardrails(raw_hi, language="Hindi (Devanagari script)")
        self.assertNotIn("ट्रायल क्लास बुक हो गई", clean_hi)
        self.assertIn("Sensationz ऐप", clean_hi)
        self.assertIn("Confirm Trial Enrollment", clean_hi)
        self.assertIsNotNone(followup_hi)

    def test_enrollment_confirmation_sanitization(self):
        """AI must strictly redact and replace any course enrollment/seat confirmation."""
        # Hinglish
        raw_hinglish = "Aapka enrollment confirm ho gaya hai aur aapki seat book ho gayi hai!"
        clean, followup = _sanitize_confirmation_guardrails(raw_hinglish, language="Hinglish (Latin script)")
        self.assertNotIn("enrollment confirm ho gaya", clean)
        self.assertNotIn("seat book ho gayi", clean)
        self.assertIn("Sensationz App", clean)
        self.assertIn("checkout", clean)
        self.assertIsNone(followup)

        # English
        raw_en = "Congratulations, your enrollment has been confirmed and your seat is reserved!"
        clean_en, followup_en = _sanitize_confirmation_guardrails(raw_en, language="English")
        self.assertNotIn("enrollment has been confirmed", clean_en)
        self.assertNotIn("seat is reserved", clean_en)
        self.assertIn("checkout", clean_en)
        self.assertIsNone(followup_en)

        # Devanagari
        raw_hi = "बधाई हो, आपका एडमिशन कन्फर्म हो गया है और सीट बुक हो गई है।"
        clean_hi, followup_hi = _sanitize_confirmation_guardrails(raw_hi, language="Hindi (Devanagari script)")
        self.assertNotIn("एडमिशन कन्फर्म हो गया", clean_hi)
        self.assertNotIn("सीट बुक हो गई", clean_hi)
        self.assertIn("चेकआउट", clean_hi)
        self.assertIsNone(followup_hi)

    def test_safe_guidance_no_false_positives(self):
        """Safe instructional guidance must NOT be altered by guardrails."""
        safe_instructions = [
            "Download the Sensationz App, go to Profile -> Trial Links and tap Confirm Trial Enrollment.",
            "Aapka profile registration confirm ho gaya hai. Aapka coupon code YOGA70 hai.",
            "You can choose 1 Month or 3 Months package and complete checkout on the app.",
            "Which batch timing works best for your trial class? 😊",
            "हमारे लाइव बैचेस सोमवार से शुक्रवार तक चलते हैं।",
        ]
        for s in safe_instructions:
            clean, _ = _sanitize_confirmation_guardrails(s, language="English")
            self.assertEqual(clean, s, f"False positive occurred on safe text: {s}")

    async def test_end_to_end_incoming_hallucination_intercepted(self):
        """Simulate LLM generating an accidental confirmation, verify it is intercepted."""
        fake_llm_json = (
            '{"reply": "Aapka payment receive ho gaya hai aur enrollment confirm ho gaya hai!", '
            '"followup": "Kal subah 7 baje class mein milte hain!", "buttons": ["Okay"]}'
        )

        with patch("src.core.conversation_handler._get_reply_llm") as mock_llm_fn:
            mock_llm = AsyncMock()
            mock_llm.ainvoke.return_value = AIMessage(content=fake_llm_json)
            mock_llm_fn.return_value = mock_llm

            state = {"course": "yoga", "stage": "NEW", "language": "Hinglish (Latin script)"}
            res = await handle_incoming_message(
                user_message="Maine payment kar diya check karo",
                session_state=state,
                conversation_history=[],
                course_id="yoga",
            )

            reply = res["reply"]
            # Must be sanitized
            self.assertNotIn("payment receive ho gaya", reply)
            self.assertNotIn("enrollment confirm ho gaya", reply)
            self.assertIn("AI assistant", reply)
            self.assertIn("accounts team", reply)


if __name__ == "__main__":
    unittest.main()
