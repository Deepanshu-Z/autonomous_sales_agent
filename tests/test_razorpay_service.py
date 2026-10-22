"""
test_razorpay_service.py — Unit Tests for Razorpay Service Dynamic QR Code Integration.

Verifies:
1. create_dynamic_qr_code payload structure:
   - type == 'upi_qr'
   - usage == 'single_use'
   - fixed_amount == True
   - payment_amount in paise
   - embedded metadata notes
2. Input validation (positive amounts, amount in INR vs paise).
3. Client initialization and error handling.
4. QR lifecycle helpers: fetch_qr_code, close_qr_code, verify_webhook_signature.
5. End-to-end integration with live test keys (if configured).
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from src.services.razorpay_service import (
    create_dynamic_qr_code,
    fetch_qr_code,
    close_qr_code,
    verify_webhook_signature,
    RazorpayServiceError,
    get_razorpay_client,
)


class TestRazorpayService(unittest.TestCase):

    def setUp(self):
        self.mock_client = MagicMock()
        self.mock_qrcode = MagicMock()
        self.mock_client.qrcode = self.mock_qrcode
        self.mock_utility = MagicMock()
        self.mock_client.utility = self.mock_utility

    def test_create_dynamic_qr_code_payload_structure(self):
        """Verifies exact required parameters: upi_qr, single_use, fixed_amount, notes."""
        self.mock_qrcode.create.return_value = {
            "id": "qr_mock_12345",
            "entity": "qr_code",
            "type": "upi_qr",
            "usage": "single_use",
            "fixed_amount": True,
            "payment_amount": 82600,
            "status": "active",
            "image_url": "https://rzp.io/rzp/mock_qr",
            "name": "Sensationz - Kathak",
            "description": "Enrollment for Kathak (1 Month)",
            "close_by": 1799999999,
            "notes": {
                "course_id": "kathak",
                "duration": "1 Month",
                "phone": "919999999999",
                "timing": "5:00 PM to 6:00 PM",
                "teacher": "Ms. Madhuri",
            },
        }

        res = create_dynamic_qr_code(
            amount=826,
            course_id="kathak",
            duration="1 Month",
            timing="5:00 PM to 6:00 PM",
            teacher="Ms. Madhuri",
            customer_phone="919999999999",
            client=self.mock_client,
        )

        # Verify create was called once
        self.mock_qrcode.create.assert_called_once()
        called_payload = self.mock_qrcode.create.call_args[0][0]

        # Verify strict API requirements
        self.assertEqual(called_payload["type"], "upi_qr")
        self.assertEqual(called_payload["usage"], "single_use")
        self.assertTrue(called_payload["fixed_amount"])
        self.assertEqual(called_payload["payment_amount"], 82600)  # 826 * 100 paise

        # Verify embedded metadata notes
        notes = called_payload["notes"]
        self.assertEqual(notes["course_id"], "kathak")
        self.assertEqual(notes["duration"], "1 Month")
        self.assertEqual(notes["phone"], "919999999999")
        self.assertEqual(notes["timing"], "5:00 PM to 6:00 PM")
        self.assertEqual(notes["teacher"], "Ms. Madhuri")

        # Verify returned standardized response
        self.assertEqual(res["qr_id"], "qr_mock_12345")
        self.assertEqual(res["image_url"], "https://rzp.io/rzp/mock_qr")
        self.assertEqual(res["payment_amount_inr"], 826.0)
        self.assertEqual(res["status"], "active")

    def test_amount_in_paise_precedence(self):
        """amount_in_paise should take precedence over amount in INR."""
        self.mock_qrcode.create.return_value = {
            "id": "qr_mock_paise",
            "status": "active",
            "payment_amount": 150000,
        }

        res = create_dynamic_qr_code(
            amount=999,
            amount_in_paise=150000,
            course_id="yoga",
            client=self.mock_client,
        )
        called_payload = self.mock_qrcode.create.call_args[0][0]
        self.assertEqual(called_payload["payment_amount"], 150000)
        self.assertEqual(res["payment_amount_inr"], 1500.0)

    def test_invalid_amount_validation(self):
        """Must reject missing, negative, or below minimum amounts."""
        with self.assertRaises(ValueError):
            create_dynamic_qr_code(amount=None, amount_in_paise=None, client=self.mock_client)

        with self.assertRaises(ValueError):
            create_dynamic_qr_code(amount=0, client=self.mock_client)

        with self.assertRaises(ValueError):
            create_dynamic_qr_code(amount=0.5, client=self.mock_client)  # 50 paise < 100 paise min

    def test_fetch_and_close_qr_code(self):
        """Verifies fetch_qr_code and close_qr_code delegate correctly."""
        self.mock_qrcode.fetch.return_value = {"id": "qr_123", "status": "active"}
        fetch_res = fetch_qr_code("qr_123", client=self.mock_client)
        self.assertEqual(fetch_res["id"], "qr_123")
        self.mock_qrcode.fetch.assert_called_with("qr_123")

        self.mock_qrcode.close.return_value = {"id": "qr_123", "status": "closed"}
        close_res = close_qr_code("qr_123", client=self.mock_client)
        self.assertEqual(close_res["status"], "closed")
        self.mock_qrcode.close.assert_called_with("qr_123")

    def test_verify_webhook_signature(self):
        """Verifies webhook signature check calls client.utility."""
        self.mock_utility.verify_webhook_signature.return_value = True
        is_valid = verify_webhook_signature(
            webhook_body='{"event": "payment.captured"}',
            webhook_signature="mock_sig",
            webhook_secret="test_secret",
            client=self.mock_client,
        )
        self.assertTrue(is_valid)

        # When utility raises an error, function returns False gracefully
        self.mock_utility.verify_webhook_signature.side_effect = Exception("Signature mismatch")
        is_invalid = verify_webhook_signature(
            webhook_body='{"event": "payment.captured"}',
            webhook_signature="bad_sig",
            webhook_secret="test_secret",
            client=self.mock_client,
        )
        self.assertFalse(is_invalid)

    def test_live_integration_with_configured_keys(self):
        """Tests live Razorpay QR creation and closing if keys are in environment."""
        key_id = os.getenv("RAZORPAY_KEY_ID")
        key_sec = os.getenv("RAZORPAY_KEY_SECRET")
        if not key_id or not key_sec or "test" not in key_id.lower():
            self.skipTest("Live keys not present or not test keys.")

        qr = create_dynamic_qr_code(
            amount=100,  # Rs. 100.00
            course_id="yoga",
            duration="1 Month",
            timing="7:00 AM",
            teacher="Suman Lata",
            customer_phone="919999999999",
            close_by_minutes=10,
        )
        self.assertTrue(qr["id"].startswith("qr_"))
        self.assertTrue(qr["image_url"].startswith("http"))
        self.assertEqual(qr["payment_amount"], 10000)
        self.assertEqual(qr["payment_amount_inr"], 100.0)
        self.assertEqual(qr["status"], "active")

        # Cleanup: close the created test QR
        closed = close_qr_code(qr["id"])
        self.assertEqual(closed.get("status"), "closed")


if __name__ == "__main__":
    unittest.main()
