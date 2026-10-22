"""
test_razorpay_webhook.py — Test Suite for Module 6: Razorpay Webhook & Payment Reconciliation.

Covers:
Subtask 6.1 — Add Webhook Route in src/api/routes.py:
- Verification of X-Razorpay-Signature header against raw body bytes.
- Rejection of invalid / missing signatures with HTTP 400.
- Rejection of malformed JSON with HTTP 400.
- Filtering of events: processes 'payment.captured' and 'qr_code.credited', ignores non-payment events with HTTP 200.

Subtask 6.2 — Payment Fulfillment & WhatsApp Confirmation:
- Updates database/session state to payment_status="PAID" and stage="ENROLLED".
- Synchronizes course, duration, timing, and instructor from notes or state.
- Dispatches instant WhatsApp confirmation template with onboarding class details and app links.
- Strictly enforces idempotency to prevent duplicate fulfillment or duplicate WhatsApp messaging.
- Verifies normalized phone number resolution (e.g., wa_919999999999, 919999999999, +919999999999).
"""

import os
import sys
import json
import hmac
import hashlib
import unittest
from unittest.mock import MagicMock, patch, AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from fastapi.testclient import TestClient
from main import app
from src.services.razorpay_service import (
    verify_webhook_signature,
    format_onboarding_confirmation,
    fulfill_razorpay_payment_async,
)


def _compute_hmac(body: str, secret: str) -> str:
    """Helper to compute valid Razorpay webhook signature."""
    return hmac.new(
        key=secret.encode("utf-8"),
        msg=body.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()


class TestRazorpayWebhook(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.client = TestClient(app)
        self.webhook_secret = "test_webhook_secret_xyz123"
        os.environ["RAZORPAY_WEBHOOK_SECRET"] = self.webhook_secret

    def test_signature_verification_rejects_missing_signature(self):
        """Must return 400 if X-Razorpay-Signature is missing."""
        payload = json.dumps({"event": "payment.captured"})
        resp = self.client.post(
            "/api/razorpay/webhook",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertEqual(data.get("status"), "invalid_signature")

    def test_signature_verification_rejects_invalid_signature(self):
        """Must return 400 if X-Razorpay-Signature does not match body HMAC."""
        payload = json.dumps({"event": "payment.captured"})
        resp = self.client.post(
            "/api/razorpay/webhook",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "X-Razorpay-Signature": "invalid_signature_hex",
            },
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json().get("status"), "invalid_signature")

    def test_signature_verification_rejects_malformed_json(self):
        """Must return 400 if body is not valid JSON even with valid HMAC."""
        raw_body = "not a valid json string {{"
        valid_sig = _compute_hmac(raw_body, self.webhook_secret)

        resp = self.client.post(
            "/api/razorpay/webhook",
            data=raw_body,
            headers={
                "Content-Type": "application/json",
                "X-Razorpay-Signature": valid_sig,
            },
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json().get("status"), "invalid_json")

    def test_event_filtering_ignores_non_payment_events(self):
        """Events other than payment.captured / qr_code.credited should return 200 with status=ignored."""
        payload = json.dumps({"event": "order.paid", "payload": {}})
        valid_sig = _compute_hmac(payload, self.webhook_secret)

        resp = self.client.post(
            "/api/razorpay/webhook",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "X-Razorpay-Signature": valid_sig,
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("status"), "ignored")
        self.assertEqual(resp.json().get("event"), "order.paid")

    def test_format_onboarding_confirmation_english(self):
        """Verifies English onboarding confirmation receipt formatting."""
        receipt = format_onboarding_confirmation(
            course_name="Kathak Dance",
            duration="3 Months",
            timing="5:00 PM to 6:00 PM",
            teacher="Ms. Madhuri",
            amount_inr=1750.0,
            payment_id="pay_test12345",
            language="english",
        )
        self.assertIn("Payment Successful & Enrollment Confirmed!", receipt)
        self.assertIn("Kathak Dance", receipt)
        self.assertIn("3 Months", receipt)
        self.assertIn("5:00 PM to 6:00 PM", receipt)
        self.assertIn("Ms. Madhuri", receipt)
        self.assertIn("₹1,750.00", receipt)
        self.assertIn("pay_test12345", receipt)
        self.assertIn("play.google.com", receipt)
        self.assertIn("apps.apple.com", receipt)

    def test_format_onboarding_confirmation_hindi(self):
        """Verifies Hindi onboarding confirmation receipt formatting."""
        receipt = format_onboarding_confirmation(
            course_name="कथक",
            duration="1 Month",
            timing="7:00 AM",
            teacher="सुमन लता",
            amount_inr=826.0,
            payment_id="pay_test_hindi",
            language="hindi",
        )
        self.assertIn("फीस पेमेंट सफल और एडमिशन कन्फर्म!", receipt)
        self.assertIn("कथक", receipt)
        self.assertIn("₹826.00", receipt)
        self.assertIn("pay_test_hindi", receipt)
        self.assertIn("play.google.com", receipt)

    @patch("src.services.razorpay_service.save_user_state_async", new_callable=AsyncMock)
    @patch("src.services.razorpay_service.get_user_state_async", new_callable=AsyncMock)
    @patch("src.services.razorpay_service.send_text_message_async", new_callable=AsyncMock)
    @patch("src.services.razorpay_service.save_message_async", new_callable=AsyncMock)
    @patch("src.services.razorpay_service.log_message_async", new_callable=AsyncMock)
    async def test_qr_code_credited_fulfillment_flow(
        self,
        mock_log_msg,
        mock_save_msg,
        mock_send_txt,
        mock_get_state,
        mock_save_state,
    ):
        """
        Subtask 6.2: Verifies qr_code.credited event updates database state to PAID,
        advances stage to ENROLLED, and sends instant WhatsApp receipt.
        """
        mock_get_state.return_value = {
            "phone": "919876543210",
            "stage": "PAYMENT_QR_SENT",
            "course": "kathak",
            "package": "1 Month",
            "timing": "5:00 PM to 6:00 PM",
            "selected_teacher": "Ms. Madhuri",
            "payment_status": "NONE",
            "language": "English",
        }

        event_payload = {
            "entity": "event",
            "event": "qr_code.credited",
            "payload": {
                "qr_code": {
                    "entity": {
                        "id": "qr_test_999",
                        "payment_amount": 82600,
                        "notes": {
                            "phone": "919876543210",
                            "course_id": "kathak",
                            "duration": "1 Month",
                            "timing": "5:00 PM to 6:00 PM",
                            "teacher": "Ms. Madhuri",
                        },
                    }
                },
                "payment": {
                    "entity": {
                        "id": "pay_test_qr_123",
                        "amount": 82600,
                        "currency": "INR",
                        "status": "captured",
                        "method": "upi",
                        "contact": "+919876543210",
                    }
                },
            },
        }

        result = await fulfill_razorpay_payment_async(event_payload)

        # 1. Verify fulfillment result
        self.assertEqual(result["status"], "fulfilled")
        self.assertEqual(result["payment_id"], "pay_test_qr_123")
        self.assertEqual(result["amount_inr"], 826.0)
        self.assertEqual(result["payment_status"], "PAID")
        self.assertEqual(result["stage"], "ENROLLED")

        # 2. Verify state was updated and saved
        mock_save_state.assert_called_once()
        saved_phone, saved_state = mock_save_state.call_args[0]
        self.assertEqual(saved_phone, "919876543210")
        self.assertEqual(saved_state["payment_status"], "PAID")
        self.assertEqual(saved_state["stage"], "ENROLLED")
        self.assertTrue(saved_state["is_enrolled"])
        self.assertEqual(saved_state["paid_amount"], 826.0)
        self.assertEqual(saved_state["payment_id"], "pay_test_qr_123")
        self.assertEqual(saved_state["order_id"], "qr_test_999")

        # 3. Verify WhatsApp confirmation was dispatched
        mock_send_txt.assert_called_once()
        dest_phone, msg_text = mock_send_txt.call_args[0]
        self.assertEqual(dest_phone, "919876543210")
        self.assertIn("Payment Successful & Enrollment Confirmed!", msg_text)
        self.assertIn("Kathak", msg_text)
        self.assertIn("₹826.00", msg_text)
        self.assertIn("pay_test_qr_123", msg_text)

        # 4. Verify message logged to chat history
        mock_save_msg.assert_called_once_with("919876543210", "assistant", msg_text)
        mock_log_msg.assert_called_once_with("919876543210", "assistant", msg_text)

    @patch("src.services.razorpay_service.save_user_state_async", new_callable=AsyncMock)
    @patch("src.services.razorpay_service.get_user_state_async", new_callable=AsyncMock)
    @patch("src.services.razorpay_service.send_text_message_async", new_callable=AsyncMock)
    async def test_payment_captured_fulfillment_flow(
        self,
        mock_send_txt,
        mock_get_state,
        mock_save_state,
    ):
        """
        Subtask 6.2: Verifies payment.captured event reconciles and confirms enrollment.
        """
        mock_get_state.return_value = {
            "phone": "918888888888",
            "stage": "PACKAGE_SELECTED",
            "course": "yoga",
            "package": "3 Months",
            "timing": "7:00 AM to 8:00 AM",
            "selected_teacher": "Suman Lata",
            "payment_status": "NONE",
            "language": "English",
        }

        event_payload = {
            "entity": "event",
            "event": "payment.captured",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_direct_captured_456",
                        "amount": 250000,  # ₹2500.00
                        "currency": "INR",
                        "status": "captured",
                        "method": "upi",
                        "contact": "+918888888888",
                        "notes": {
                            "phone": "918888888888",
                            "course_id": "yoga",
                            "duration": "3 Months",
                            "timing": "7:00 AM to 8:00 AM",
                            "teacher": "Suman Lata",
                        },
                    }
                }
            },
        }

        result = await fulfill_razorpay_payment_async(event_payload)

        self.assertEqual(result["status"], "fulfilled")
        self.assertEqual(result["payment_id"], "pay_direct_captured_456")
        self.assertEqual(result["amount_inr"], 2500.0)
        self.assertEqual(result["stage"], "ENROLLED")

        # Verify state updated
        mock_save_state.assert_called_once()
        _, saved_state = mock_save_state.call_args[0]
        self.assertEqual(saved_state["payment_status"], "PAID")
        self.assertEqual(saved_state["stage"], "ENROLLED")
        self.assertEqual(saved_state["paid_amount"], 2500.0)

        # Verify receipt sent
        mock_send_txt.assert_called_once()
        dest_phone, msg_text = mock_send_txt.call_args[0]
        self.assertEqual(dest_phone, "918888888888")
        self.assertIn("₹2,500.00", msg_text)
        self.assertIn("pay_direct_captured_456", msg_text)

    @patch("src.services.razorpay_service.save_user_state_async", new_callable=AsyncMock)
    @patch("src.services.razorpay_service.get_user_state_async", new_callable=AsyncMock)
    @patch("src.services.razorpay_service.send_text_message_async", new_callable=AsyncMock)
    async def test_idempotency_prevents_duplicate_fulfillment_and_messaging(
        self,
        mock_send_txt,
        mock_get_state,
        mock_save_state,
    ):
        """
        Subtask 6.2 Idempotency: Duplicate webhook calls for the same payment must not re-fulfill
        or send duplicate WhatsApp confirmation messages.
        """
        # User is ALREADY marked as PAID with this payment_id
        mock_get_state.return_value = {
            "phone": "919999999999",
            "stage": "ENROLLED",
            "payment_status": "PAID",
            "payment_id": "pay_already_fulfilled_123",
            "order_id": "qr_existing_123",
            "paid_amount": 826.0,
        }

        duplicate_payload = {
            "entity": "event",
            "event": "payment.captured",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_already_fulfilled_123",
                        "amount": 82600,
                        "status": "captured",
                        "contact": "+919999999999",
                        "notes": {"phone": "919999999999"},
                    }
                }
            },
        }

        result = await fulfill_razorpay_payment_async(duplicate_payload)

        # Must report already_fulfilled
        self.assertEqual(result["status"], "already_fulfilled")
        self.assertEqual(result["payment_id"], "pay_already_fulfilled_123")

        # Must NOT re-save state or re-send WhatsApp message
        mock_save_state.assert_not_called()
        mock_send_txt.assert_not_called()

    @patch("src.api.routes.fulfill_razorpay_payment_async", new_callable=AsyncMock)
    def test_end_to_end_webhook_route_with_valid_signature(self, mock_fulfill):
        """
        Subtask 6.1 End-to-End: POST /api/razorpay/webhook with valid signature
        calls fulfill_razorpay_payment_async and returns HTTP 200 with result.
        """
        mock_fulfill.return_value = {
            "status": "fulfilled",
            "phone": "919999999999",
            "payment_id": "pay_e2e_test",
            "stage": "ENROLLED",
        }

        payload_dict = {
            "event": "payment.captured",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_e2e_test",
                        "amount": 82600,
                    }
                }
            },
        }
        raw_body = json.dumps(payload_dict)
        valid_signature = _compute_hmac(raw_body, self.webhook_secret)

        resp = self.client.post(
            "/api/razorpay/webhook",
            data=raw_body,
            headers={
                "Content-Type": "application/json",
                "X-Razorpay-Signature": valid_signature,
            },
        )

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data.get("status"), "ok")
        self.assertEqual(data.get("event"), "payment.captured")
        self.assertEqual(data.get("result", {}).get("payment_id"), "pay_e2e_test")
        mock_fulfill.assert_called_once_with(payload_dict)

    @patch("src.api.routes.fulfill_razorpay_payment_async", new_callable=AsyncMock)
    def test_test_razorpay_webhook_endpoint_bypasses_signature(self, mock_fulfill):
        """Verifies /test-razorpay-webhook processes without signature check."""
        mock_fulfill.return_value = {"status": "fulfilled"}
        payload = {"event": "qr_code.credited", "payload": {}}

        resp = self.client.post("/test-razorpay-webhook", json=payload)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("status"), "ok")
        mock_fulfill.assert_called_once_with(payload)


if __name__ == "__main__":
    unittest.main()
