import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from src.core.conversation_handler import handle_conversation_turn_async


class TestPayOnlineQRDispatch(unittest.IsolatedAsyncioTestCase):

    async def test_pay_online_dispatches_qr_when_all_5_prerequisites_met(self):
        """When user selects Course, Timing, Teacher, Package, and exact Fee, saying 'Pay Online' dispatches QR."""
        phone = "919876543210"
        mock_state = {
            "course": "yoga",
            "package": "1 Month",
            "selected_duration": "1 Month",
            "timing": "6:00 AM to 7:00 AM",
            "selected_timing": "6:00 AM to 7:00 AM",
            "teacher": "Suman Lata",
            "selected_teacher": "Suman Lata",
            "language": "Hinglish",
            "stage": "PACKAGE_SELECTED",
        }

        mock_qr_data = {
            "id": "qr_test_yoga_123",
            "qr_id": "qr_test_yoga_123",
            "image_url": "https://rzp.io/rzp/test_yoga_img",
            "status": "active",
            "payment_amount": 73500,
            "payment_amount_inr": 735.0,
            "name": "Sensationz - Online Live Interactive Yoga",
            "description": "Yoga 1 Month Pack",
        }

        with patch("src.core.conversation_handler.get_user_state_async", return_value=mock_state), \
             patch("src.core.conversation_handler.save_user_state_async", new_callable=AsyncMock), \
             patch("src.core.conversation_handler.save_message_async", new_callable=AsyncMock), \
             patch("src.core.conversation_handler.log_message_async", new_callable=AsyncMock), \
             patch("src.services.interakt.send_text_message_async", new_callable=AsyncMock) as mock_send_txt, \
             patch("src.services.interakt.send_image_message_async", new_callable=AsyncMock) as mock_send_img, \
             patch("src.services.razorpay_service.create_dynamic_qr_code", return_value=mock_qr_data) as mock_create_qr:

            mock_send_img.return_value = True

            res = await handle_conversation_turn_async(
                phone=phone,
                text="Pay Online",
            )

            self.assertIsNotNone(res)
            # Verify QR code was generated
            mock_create_qr.assert_called_once()
            called_kwargs = mock_create_qr.call_args[1]
            self.assertEqual(called_kwargs["course_id"], "yoga")
            self.assertEqual(called_kwargs["duration"], "1 Month")

            # Verify image was delivered
            mock_send_img.assert_called_once()
            self.assertEqual(mock_send_img.call_args[0][0], phone)
            self.assertEqual(mock_send_img.call_args[0][1], "https://rzp.io/rzp/test_yoga_img")

    async def test_pay_online_blocked_when_timing_missing(self):
        """When timing is missing, saying 'Pay Online' must NOT dispatch QR and buttons must prompt for timing."""
        phone = "919876543210"
        mock_state = {
            "course": "yoga",
            "package": "1 Month",
            "selected_duration": "1 Month",
            "timing": None,
            "selected_timing": None,
            "teacher": "Suman Lata",
            "selected_teacher": "Suman Lata",
            "language": "Hinglish",
            "stage": "PACKAGE_SELECTED",
        }

        with patch("src.core.conversation_handler.get_user_state_async", return_value=mock_state), \
             patch("src.core.conversation_handler.save_user_state_async", new_callable=AsyncMock), \
             patch("src.core.conversation_handler.save_message_async", new_callable=AsyncMock), \
             patch("src.core.conversation_handler.log_message_async", new_callable=AsyncMock), \
             patch("src.services.interakt.send_text_message_async", new_callable=AsyncMock) as mock_send_txt, \
             patch("src.services.interakt.send_image_message_async", new_callable=AsyncMock) as mock_send_img, \
             patch("src.services.razorpay_service.create_dynamic_qr_code") as mock_create_qr:

            res = await handle_conversation_turn_async(
                phone=phone,
                text="Pay Online",
            )

            self.assertIsNotNone(res)
            # QR code MUST NOT be generated
            mock_create_qr.assert_not_called()
            mock_send_img.assert_not_called()

            # Buttons must NOT have 'Pay Online' - they must offer timing options
            buttons = res.get("buttons") or []
            self.assertFalse(any("pay" in b.lower() for b in buttons))
            self.assertTrue(any("batch" in b.lower() or "timing" in b.lower() or "am" in b.lower() or "pm" in b.lower() for b in buttons))


if __name__ == "__main__":
    unittest.main()
