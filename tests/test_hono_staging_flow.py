"""
test_hono_staging_flow.py — Comprehensive Verification Suite for
Hono Auto-Enrollment & Trial Staging Pipeline via WhatsApp.
"""

import unittest
from unittest.mock import patch, AsyncMock, MagicMock

from src.services.hono_staging_client import (
    clean_email,
    clean_phone_for_contact,
    stage_ai_enrollment_async,
)
from src.services.razorpay_service import (
    format_onboarding_confirmation,
    fulfill_razorpay_payment_async,
    create_dynamic_qr_code,
)
from src.core.conversation_handler import (
    handle_incoming_message,
    _sanitize_confirmation_guardrails,
    _ensure_app_website_links,
)
from src.state.chat_state import reset_user_state


class TestHonoStagingClient(unittest.TestCase):
    def test_clean_email_variants(self):
        self.assertEqual(clean_email("rahul.sharma@gmail.com"), "rahul.sharma@gmail.com")
        self.assertEqual(clean_email("My email is Priya99@Yahoo.com."), "priya99@yahoo.com")
        self.assertEqual(clean_email("ANITA_DANCE@GMAIL.COM"), "anita_dance@gmail.com")
        self.assertIsNone(clean_email("no email here"))
        self.assertIsNone(clean_email(""))

    def test_clean_phone_for_contact(self):
        self.assertEqual(clean_phone_for_contact("9876543210"), "919876543210")
        self.assertEqual(clean_phone_for_contact("+91-98765-43210"), "919876543210")
        self.assertEqual(clean_phone_for_contact("919876543210"), "919876543210")


class TestHonoAsyncClientCalls(unittest.IsolatedAsyncioTestCase):
    async def test_stage_ai_enrollment_validation_failure(self):
        # Invalid email
        res = await stage_ai_enrollment_async(
            email="invalid_email",
            contact="9876543210",
            course="Kathak",
        )
        self.assertFalse(res["success"])
        self.assertEqual(res["action"], "ERROR")

        # Invalid contact
        res2 = await stage_ai_enrollment_async(
            email="valid@gmail.com",
            contact="123",
            course="Kathak",
        )
        self.assertFalse(res2["success"])
        self.assertEqual(res2["action"], "ERROR")

    @patch("httpx.AsyncClient.post")
    async def test_stage_ai_enrollment_success(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {
            "success": True,
            "message": "Staged successfully",
            "data": {
                "action": "STAGED",
                "staged_id": "test-uuid-1234",
            },
        }
        mock_post.return_value = mock_resp

        res = await stage_ai_enrollment_async(
            email="student@gmail.com",
            contact="9876543210",
            course="Kathak",
            timing="5:00 PM to 6:00 PM",
            teacher="Sneha Maurya",
            days="Tuesday & Thursday",
            days_code="TT",
            enrollment_type="COURSE",
            payment_id="pay_12345",
            amount=826.0,
        )
        self.assertTrue(res["success"])
        self.assertEqual(res["action"], "STAGED")
        self.assertEqual(res["staged_id"], "test-uuid-1234")


class TestRazorpayWebhookStaging(unittest.IsolatedAsyncioTestCase):
    def test_create_dynamic_qr_code_includes_days(self):
        mock_client = MagicMock()
        mock_client.qrcode.create.return_value = {
            "id": "qr_test123",
            "image_url": "https://rzp.io/qr.png",
            "status": "active",
        }
        qr = create_dynamic_qr_code(
            amount=826,
            customer_phone="9876543210",
            course_id="kathak",
            duration="1 Month",
            timing="5:00 PM to 6:00 PM",
            teacher="Sneha Maurya",
            days="Tuesday & Thursday",
            client=mock_client,
        )
        call_args = mock_client.qrcode.create.call_args[0][0]
        self.assertEqual(call_args["notes"]["days"], "Tuesday & Thursday")

    @patch("src.services.razorpay_service.send_text_message_async", new_callable=AsyncMock)
    @patch("src.services.hono_staging_client.stage_ai_enrollment_async", new_callable=AsyncMock)
    async def test_fulfill_payment_with_email_auto_stages(self, mock_stage, mock_send):
        phone = "919876543210"
        reset_user_state(phone)
        mock_stage.return_value = {
            "success": True,
            "action": "STAGED",
            "staged_id": "staged-uuid-999",
        }

        payload = {
            "event": "payment.captured",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_test_001",
                        "amount": 82600,
                        "contact": phone,
                        "email": "priya.dance@gmail.com",
                        "notes": {
                            "source": "whatsapp_sales_agent",
                            "course_id": "kathak",
                            "duration": "1 Month",
                            "timing": "6:00 PM to 7:00 PM",
                            "teacher": "Sneha Maurya",
                            "days": "Tuesday & Thursday",
                            "days_code": "TT",
                        },
                    }
                }
            },
        }

        res = await fulfill_razorpay_payment_async(payload, send_notification=True)
        self.assertEqual(res["status"], "fulfilled")
        self.assertTrue(res["staged_enrollment"])
        self.assertEqual(res["student_email"], "priya.dance@gmail.com")
        self.assertFalse(res["awaiting_email_fulfillment"])
        mock_stage.assert_called_once()

        # Check confirmation message mentions Google Sign-In and email
        sent_text = mock_send.call_args[0][1]
        self.assertIn("priya.dance@gmail.com", sent_text)
        self.assertIn("Continue with Google", sent_text)

    @patch("src.services.razorpay_service.send_text_message_async", new_callable=AsyncMock)
    @patch("src.services.hono_staging_client.stage_ai_enrollment_async", new_callable=AsyncMock)
    async def test_fulfill_payment_without_email_requests_gmail(self, mock_stage, mock_send):
        phone = "919999900001"
        reset_user_state(phone)
        payload = {
            "event": "payment.captured",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_test_002",
                        "amount": 82600,
                        "contact": phone,
                        # No email provided (UPI QR scan)
                        "notes": {
                            "source": "whatsapp_sales_agent",
                            "course_id": "yoga",
                            "duration": "1 Month",
                            "timing": "7:00 AM to 8:00 AM",
                            "teacher": "Suman Lata",
                        },
                    }
                }
            },
        }

        res = await fulfill_razorpay_payment_async(payload, send_notification=True)
        self.assertEqual(res["status"], "fulfilled")
        self.assertFalse(res["staged_enrollment"])
        self.assertTrue(res["awaiting_email_fulfillment"])
        mock_stage.assert_not_called()

        sent_text = mock_send.call_args[0][1]
        self.assertIn("Gmail", sent_text)


class TestConversationHandlerGmailStaging(unittest.IsolatedAsyncioTestCase):
    @patch("src.core.intent_classifier.extract_intent_and_state_changes", new_callable=AsyncMock)
    @patch("src.services.hono_staging_client.stage_ai_enrollment_async", new_callable=AsyncMock)
    async def test_paid_user_sends_gmail_triggers_course_staging(self, mock_stage, mock_extract):
        mock_extract.return_value = {
            "customer_intent": "OTHER",
            "detected_language": "English",
        }
        mock_stage.return_value = {
            "success": True,
            "action": "STAGED",
            "staged_id": "staged-course-123",
        }

        session_state = {
            "phone": "919876543255",
            "course": "kathak",
            "timing": "5:00 PM to 6:00 PM",
            "teacher": "Sneha Maurya",
            "days": "Tuesday & Thursday",
            "days_code": "TT",
            "package": "1 Month",
            "payment_status": "PAID",
            "is_enrolled": True,
            "awaiting_email_fulfillment": True,
            "payment_id": "pay_xyz_123",
            "paid_amount": 826.0,
            "language": "English",
        }

        result = await handle_incoming_message(
            user_message="my email is rahul99@gmail.com",
            session_state=session_state,
            conversation_history=[],
            course_id="kathak",
        )

        self.assertTrue(result["new_state"].get("staged_enrollment"))
        self.assertEqual(result["new_state"].get("student_email"), "rahul99@gmail.com")
        self.assertFalse(result["new_state"].get("awaiting_email_fulfillment"))
        self.assertIn("Continue with Google", result["reply"])
        self.assertIn("rahul99@gmail.com", result["reply"])
        mock_stage.assert_called_once()
        call_kwargs = mock_stage.call_args[1]
        self.assertEqual(call_kwargs["enrollment_type"], "COURSE")
        self.assertEqual(call_kwargs["email"], "rahul99@gmail.com")

    @patch("src.core.intent_classifier.extract_intent_and_state_changes", new_callable=AsyncMock)
    @patch("src.services.hono_staging_client.stage_ai_enrollment_async", new_callable=AsyncMock)
    async def test_trial_user_sends_gmail_triggers_trial_staging(self, mock_stage, mock_extract):
        mock_extract.return_value = {
            "customer_intent": "OTHER",
            "wants_trial": True,
            "detected_language": "English",
        }
        mock_stage.return_value = {
            "success": True,
            "action": "STAGED",
            "staged_id": "staged-trial-456",
        }

        session_state = {
            "phone": "919876543266",
            "course": "yoga",
            "timing": "7:00 AM to 8:00 AM",
            "teacher": "Suman Lata",
            "wants_trial": True,
            "stage": "TRIAL_REQUESTED",
            "language": "English",
        }

        result = await handle_incoming_message(
            user_message="please book trial anita@gmail.com",
            session_state=session_state,
            conversation_history=[],
            course_id="yoga",
        )

        self.assertTrue(result["new_state"].get("staged_trial"))
        self.assertEqual(result["new_state"].get("student_email"), "anita@gmail.com")
        self.assertEqual(result["new_state"].get("stage"), "TRIAL_CONFIRMED")
        self.assertIn("Free Trial Class is Confirmed", result["reply"])
        self.assertIn("Continue with Google", result["reply"])
        mock_stage.assert_called_once()
        call_kwargs = mock_stage.call_args[1]
        self.assertEqual(call_kwargs["enrollment_type"], "TRIAL")
        self.assertEqual(call_kwargs["email"], "anita@gmail.com")

    def test_guardrails_allow_staged_trial_and_paid_confirmations(self):
        # 1. Staged trial state should NOT be sanitized
        trial_state = {"staged_trial": True, "stage": "TRIAL_CONFIRMED"}
        reply, followup = _sanitize_confirmation_guardrails(
            reply="Your free trial class has been confirmed for 7 AM!",
            followup=None,
            language="English",
            state=trial_state,
        )
        self.assertEqual(reply, "Your free trial class has been confirmed for 7 AM!")

        # 2. Paid enrolled state should NOT be sanitized
        paid_state = {"payment_status": "PAID", "staged_enrollment": True}
        reply2, followup2 = _sanitize_confirmation_guardrails(
            reply="Your payment is received and enrollment confirmed!",
            followup=None,
            language="English",
            state=paid_state,
        )
        self.assertEqual(reply2, "Your payment is received and enrollment confirmed!")

        # 3. Unverified trial should be sanitized
        unverified_state = {}
        reply3, _ = _sanitize_confirmation_guardrails(
            reply="Your free trial has been confirmed!",
            followup=None,
            language="English",
            state=unverified_state,
        )
        self.assertIn("free trial classes cannot be booked or confirmed directly over chat", reply3)

    def test_app_links_retained_in_onboarding_messages(self):
        msg = (
            "🎉 Your Account is Successfully Linked! 🎉\n\n"
            "Download the Sensationz App:\n"
            "📱 Android: https://play.google.com/store/apps/details?id=com.sensationz.sensationz.dev\n"
            "🍎 iOS: https://apps.apple.com/us/app/sensationz/id6761418351"
        )
        result = _ensure_app_website_links(msg, user_asked_for_links=True)
        self.assertIn("play.google.com", result)
        self.assertIn("apps.apple.com", result)


if __name__ == "__main__":
    unittest.main()

