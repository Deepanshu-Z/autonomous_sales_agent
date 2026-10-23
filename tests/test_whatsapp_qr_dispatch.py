"""
test_whatsapp_qr_dispatch.py — Automated Unit Tests for Subtask 5.3 WhatsApp QR Media Dispatch.

Verifies:
1. dispatch_payment_qr_async calculates exact fee via pricing_engine and calls Razorpay QR API.
2. Formats localized payment instructions and direct UPI payment link.
3. Dispatches QR code image via send_image_message_async.
4. Gracefully falls back to text message if image sending fails.
5. Persistent session state update (qr_code_sent=True, last_qr_id, last_qr_url).
6. Anti-spam protection (avoids redundant QR sends unless forced or requested).
"""

import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from src.core.conversation_handler import dispatch_payment_qr_async


class TestWhatsAppQRDispatch(unittest.IsolatedAsyncioTestCase):

    async def test_dispatch_payment_qr_success(self):
        """Dispatches QR image with instructions and updates session state."""
        state = {
            "course": "kathak",
            "package": "1 Month",
            "timing": "5:00 PM to 6:00 PM",
            "teacher": "Ms. Madhuri",
            "language": "English",
        }
        mock_qr_data = {
            "id": "qr_test_kathak123",
            "qr_id": "qr_test_kathak123",
            "image_url": "https://rzp.io/rzp/test_kathak_img",
            "status": "active",
            "payment_amount": 82600,
            "payment_amount_inr": 826.0,
            "name": "Sensationz - Online Live Interactive Kathak",
            "description": "Kathak 1 Month Pack",
            "notes": {
                "course_id": "kathak",
                "duration": "1 Month",
                "phone": "919876543210",
                "timing": "5:00 PM to 6:00 PM",
                "teacher": "Ms. Madhuri",
            },
        }

        with patch.dict(os.environ, {"TEST_QR_AMOUNT": ""}), \
             patch("src.services.razorpay_service.create_dynamic_qr_code", return_value=mock_qr_data) as mock_create_qr, \
             patch("src.services.interakt.send_image_message_async", new_callable=AsyncMock) as mock_send_img:

            mock_send_img.return_value = True

            res = await dispatch_payment_qr_async(
                phone="919876543210",
                state=state,
                course_id="kathak",
                package="1 Month",
            )

            self.assertIsNotNone(res)
            self.assertEqual(res["id"], "qr_test_kathak123")
            mock_create_qr.assert_called_once()
            called_kwargs = mock_create_qr.call_args[1]
            self.assertEqual(called_kwargs["amount"], 826)
            self.assertEqual(called_kwargs["course_id"], "kathak")
            self.assertEqual(called_kwargs["duration"], "1 Month")

            # Verify image was dispatched with caption containing instructions and direct payment link
            mock_send_img.assert_called_once()
            call_phone, call_url = mock_send_img.call_args[0][:2]
            call_caption = mock_send_img.call_args[1].get("caption", "")
            self.assertEqual(call_phone, "919876543210")
            self.assertEqual(call_url, "https://rzp.io/rzp/test_kathak_img")
            self.assertIn("Scan & Pay via any UPI App", call_caption)
            self.assertIn("100% TRUSTED AND VERIFIED BY RAZORPAY", call_caption)

            # Verify state persistence
            self.assertTrue(state.get("qr_code_sent"))
            self.assertEqual(state.get("last_qr_id"), "qr_test_kathak123")
            self.assertEqual(state.get("last_qr_package"), "1 Month")
            self.assertEqual(state.get("last_qr_url"), "https://rzp.io/rzp/test_kathak_img")

    async def test_dispatch_fallback_to_text_if_image_fails(self):
        """If send_image_message_async fails, falls back to sending text message with link."""
        state = {
            "course": "yoga",
            "package": "1 Month",
            "timing": "6:00 AM to 7:00 AM",
            "teacher": "Suman Lata",
            "language": "English",
        }
        mock_qr_data = {
            "id": "qr_yoga_123",
            "qr_id": "qr_yoga_123",
            "image_url": "https://rzp.io/rzp/yoga_qr",
            "status": "active",
            "payment_amount": 21000,
            "payment_amount_inr": 210.0,
        }

        with patch("src.services.razorpay_service.create_dynamic_qr_code", return_value=mock_qr_data), \
             patch("src.services.interakt.send_image_message_async", new_callable=AsyncMock) as mock_send_img, \
             patch("src.core.conversation_handler.send_text_message_async", new_callable=AsyncMock) as mock_send_text:

            mock_send_img.return_value = False  # Image sending failed
            mock_send_text.return_value = True

            res = await dispatch_payment_qr_async(
                phone="919999999999",
                state=state,
                course_id="yoga",
                package="1 Month",
            )

            self.assertIsNotNone(res)
            mock_send_img.assert_called_once()
            # Verify text fallback was sent
            mock_send_text.assert_called_once()
            text_body = mock_send_text.call_args[0][1]
            self.assertIn("https://rzp.io/rzp/yoga_qr", text_body)
            self.assertTrue(state.get("qr_code_sent"))

    async def test_anti_spam_prevents_duplicate_qr(self):
        """Does not re-dispatch QR code if already sent for the same package, unless forced."""
        state = {
            "course": "kathak",
            "package": "1 Month",
            "timing": "5:00 PM to 6:00 PM",
            "teacher": "Ms. Madhuri",
            "qr_code_sent": True,
            "last_qr_package": "1 Month",
        }

        with patch("src.services.razorpay_service.create_dynamic_qr_code") as mock_create_qr:
            # 1. Without force_resend -> returns None, no API call
            res = await dispatch_payment_qr_async(
                phone="919876543210",
                state=state,
                course_id="kathak",
                package="1 Month",
                force_resend=False,
            )
            self.assertIsNone(res)
            mock_create_qr.assert_not_called()

            # 2. With force_resend=True (customer asked for QR again) -> calls API
            mock_create_qr.return_value = {
                "id": "qr_resend",
                "image_url": "https://rzp.io/rzp/resend",
                "payment_amount": 82600,
            }
            with patch("src.services.interakt.send_image_message_async", new_callable=AsyncMock, return_value=True):
                res_forced = await dispatch_payment_qr_async(
                    phone="919876543210",
                    state=state,
                    course_id="kathak",
                    package="1 Month",
                    force_resend=True,
                )
                self.assertIsNotNone(res_forced)
                mock_create_qr.assert_called_once()


if __name__ == "__main__":
    unittest.main()
