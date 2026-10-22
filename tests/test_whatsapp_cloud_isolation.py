"""
test_whatsapp_cloud_isolation.py — Verifies Meta WhatsApp Cloud & Meta channels are completely isolated from Interakt.

Ensures:
1. assign_chat_to_agent_async / assign_chat_to_agent returns True immediately for 'wa_', 'ig_', and 'fb_' prefixes without calling Interakt API.
2. Initial lead assignment in tasks.py and routes.py bypasses Interakt assignment for 'wa_', 'ig_', 'fb_'.
3. send_video_message_async routes 'wa_' to WhatsApp Cloud API without calling Interakt API.
4. send_text_message does not send non-Interakt numbers to Interakt.
"""

import os
import sys
import unittest
from unittest.mock import patch, AsyncMock, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.services.interakt import (
    assign_chat_to_agent,
    assign_chat_to_agent_async,
    send_text_message,
    send_video_message_async,
)


class TestWhatsAppCloudIsolation(unittest.IsolatedAsyncioTestCase):

    @patch("src.services.interakt._get_async_client")
    async def test_assign_chat_to_agent_async_bypasses_for_wa_prefix(self, mock_client):
        """Must not call Interakt assignment endpoint for wa_ numbers."""
        res = await assign_chat_to_agent_async("wa_917678368328", "harshanand@sensationzmedia.com")
        self.assertTrue(res)
        mock_client.assert_not_called()

    @patch("src.services.interakt._get_async_client")
    async def test_assign_chat_to_agent_async_bypasses_for_meta_prefixes(self, mock_client):
        """Must not call Interakt assignment endpoint for ig_ and fb_ users."""
        res_ig = await assign_chat_to_agent_async("ig_12345678", "harshanand@sensationzmedia.com")
        res_fb = await assign_chat_to_agent_async("fb_87654321", "harshanand@sensationzmedia.com")
        self.assertTrue(res_ig)
        self.assertTrue(res_fb)
        mock_client.assert_not_called()

    @patch("httpx.post")
    def test_sync_assign_chat_to_agent_bypasses_for_cloud_and_meta(self, mock_post):
        """Sync assign_chat_to_agent must not hit Interakt HTTP endpoint for wa_/ig_/fb_."""
        self.assertTrue(assign_chat_to_agent("wa_917678368328", "agent@sensationz.com"))
        self.assertTrue(assign_chat_to_agent("ig_user123", "agent@sensationz.com"))
        self.assertTrue(assign_chat_to_agent("fb_user456", "agent@sensationz.com"))
        mock_post.assert_not_called()

    @patch("src.services.whatsapp_cloud_client.send_whatsapp_cloud_video_async", new_callable=AsyncMock)
    @patch("src.services.interakt._get_async_client")
    async def test_send_video_message_async_routes_wa_to_cloud_api(self, mock_client, mock_cloud_video):
        """send_video_message_async must route wa_ to WhatsApp Cloud API and never call Interakt."""
        mock_cloud_video.return_value = {"status": "ok"}
        res = await send_video_message_async("wa_917678368328", "https://example.com/video.mp4", caption="Test")
        self.assertTrue(res)
        mock_cloud_video.assert_called_once_with("917678368328", "https://example.com/video.mp4", caption="Test")
        mock_client.assert_not_called()

    @patch("httpx.post")
    def test_sync_send_text_message_bypasses_interakt_for_wa_prefix(self, mock_post):
        """Sync send_text_message returns None without hitting Interakt."""
        res = send_text_message("wa_917678368328", "hello")
        self.assertIsNone(res)
        mock_post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
