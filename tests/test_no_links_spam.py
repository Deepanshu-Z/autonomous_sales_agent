import unittest
import asyncio
import os
import sys
from unittest.mock import AsyncMock, patch
from langchain_core.messages import AIMessage

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.conversation_handler import handle_conversation_turn_async
from src.state.chat_state import save_user_state_async, get_user_state_async


class TestNoLinksSpam(unittest.IsolatedAsyncioTestCase):

    async def test_step4_seat_lock_no_links_in_followup(self):
        """Step 4: Lock My Seat must provide links in Message #1 ONLY, and ZERO links in follow-up."""
        phone = "919999999991"
        state = {
            "course": "yoga",
            "stage": "PACKAGE_SELECTED",
            "package": "3 Months",
            "timing": "6:00 PM",
            "fee": 525,
            "refusal_stage": 0,
            "profile_created": False,
        }
        await save_user_state_async(phone, state)

        mock_step4_json = (
            '{"reply": "Setup: 80% Completed! Please download the Sensationz App using the links below:\\n'
            '• Android: https://play.google.com/store/apps/details?id=com.sensationz.sensationz.dev\\n'
            '• iOS: https://apps.apple.com/us/app/sensationz/id6761418351", '
            '"followup": "Google login karne ke liye app mein 1-tap karein aur yahan Done ya Yes reply karein!", '
            '"buttons": ["Google Login Done 👍"]}'
        )

        with patch("src.core.conversation_handler._get_reply_llm") as mock_get_llm, \
             patch("src.services.quick_reply_service.send_followup_with_buttons_async", new_callable=AsyncMock) as mock_send_btn, \
             patch("src.services.interakt.send_text_message_async", new_callable=AsyncMock) as mock_send_text:
            
            mock_llm = AsyncMock()
            mock_llm.ainvoke.return_value = AIMessage(content=mock_step4_json)
            mock_get_llm.return_value = mock_llm

            res = await handle_conversation_turn_async(phone, "Lock My Seat 🔒")
            
            # Follow-up message sent via quick reply buttons
            followup = res.get("followup")
            self.assertIsNotNone(followup)
            self.assertNotIn("play.google.com", followup)
            self.assertNotIn("apps.apple.com", followup)
            self.assertNotIn("shop.sensationzperformingarts.com", followup)
            self.assertIn("Google login karne ke liye", followup)

            # Message 1 should have the links
            reply = res.get("reply")
            self.assertIn("play.google.com", reply)

    async def test_step5_google_login_done_zero_links_in_both_messages(self):
        """Step 5: Google Login Done must NOT append platform links to Message #1 or Message #2."""
        phone = "8302746840"  # verified contact in prod DB
        state = {
            "course": "yoga",
            "stage": "APP_LINK_SENT",
            "package": "3 Months",
            "timing": "6:00 PM",
            "fee": 525,
            "refusal_stage": 0,
            "profile_created": False,
        }
        await save_user_state_async(phone, state)

        quoted_user_msg = (
            '[Replying to: "Google login karne ke liye app mein 1-tap karein aur yahan Done reply karein!\\n\\n'
            '📱 Android: https://play.google.com/store/apps/details?id=com.sensationz.sensationz.dev\\n'
            '🍎 iOS: https://apps.apple.com/us/app/sensationz/id6761418351\\n'
            '💻 Website / PC / Laptop: https://shop.sensationzperformingarts.com/"]\n'
            'Google Login Done 👍'
        )

        mock_step5_json = (
            '{"reply": "Congratulations! Aapka profile registration confirm ho gaya hai. Aapka coupon code YOGA600 hai.", '
            '"followup": "Kya aapko checkout par coupon apply karne mein madad chahiye?", '
            '"buttons": ["Apply Coupon 👍"]}'
        )

        with patch("src.core.conversation_handler._get_reply_llm") as mock_get_llm, \
             patch("src.services.quick_reply_service.send_followup_with_buttons_async", new_callable=AsyncMock) as mock_send_btn, \
             patch("src.services.interakt.send_text_message_async", new_callable=AsyncMock) as mock_send_text:
            
            mock_llm = AsyncMock()
            mock_llm.ainvoke.return_value = AIMessage(content=mock_step5_json)
            mock_get_llm.return_value = mock_llm

            res = await handle_conversation_turn_async(phone, quoted_user_msg)
            
            reply = res.get("reply")
            followup = res.get("followup")

            # Zero platform links in Message #1
            self.assertNotIn("play.google.com", reply)
            self.assertNotIn("apps.apple.com", reply)
            self.assertNotIn("shop.sensationzperformingarts.com", reply)
            self.assertIn("YOGA600", reply)

            # Zero platform links in Message #2 (follow-up)
            self.assertIsNotNone(followup)
            self.assertNotIn("play.google.com", followup)
            self.assertNotIn("apps.apple.com", followup)
            self.assertNotIn("shop.sensationzperformingarts.com", followup)
            self.assertIn("Kya aapko checkout par coupon apply karne mein madad chahiye?", followup)


if __name__ == "__main__":
    unittest.main()
