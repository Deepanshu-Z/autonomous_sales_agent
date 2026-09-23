"""
test_zero_links_and_media.py — Tests for zero-link policy, media dispatch, anti-spam, and fallback.
Uses standard library unittest so no pytest module is required.
"""

import unittest
import asyncio
from unittest.mock import AsyncMock, patch
from src.core.conversation_handler import (
    _ensure_app_website_links,
    dispatch_media_guide_async,
)
from src.core import kb_loader
from src.state.chat_state import get_default_state
from src.core.intent_classifier import _empty_flags


class TestZeroLinksAndMedia(unittest.TestCase):

    def test_zero_links_sanitizer_strips_all_links_when_not_asked(self):
        """Verify that _ensure_app_website_links strips all URLs when user_asked_for_links=False."""
        text_with_links = (
            "Search Sensationz on app store and create your profile.\n"
            "📱 Android: https://play.google.com/store/apps/details?id=com.sensationz.sensationz.dev\n"
            "🍎 iOS: https://apps.apple.com/us/app/sensationz/id6761418351\n"
            "💻 Website: https://shop.sensationzperformingarts.com/\n"
            "🎬 Video: https://youtube.com/shorts/wyJBvEqIsvU\n"
            "Reply Done once finished!"
        )

        cleaned = _ensure_app_website_links(text_with_links, user_asked_for_links=False)

        self.assertNotIn("play.google.com", cleaned)
        self.assertNotIn("apps.apple.com", cleaned)
        self.assertNotIn("shop.sensationzperformingarts.com", cleaned)
        self.assertNotIn("youtube.com", cleaned)
        self.assertNotIn("https://", cleaned)
        self.assertIn("Search Sensationz on app store and create your profile.", cleaned)
        self.assertIn("Reply Done once finished!", cleaned)

    def test_zero_links_sanitizer_appends_links_when_explicitly_asked(self):
        """Verify that _ensure_app_website_links appends official platform links when user_asked_for_links=True."""
        text = "Here are our official download links:"

        result = _ensure_app_website_links(text, user_asked_for_links=True)

        self.assertIn("play.google.com", result)
        self.assertIn("apps.apple.com", result)
        self.assertIn("shop.sensationzperformingarts.com", result)

    def test_dispatch_media_guide_anti_spam(self):
        """Verify that video is dispatched once, then blocked on subsequent turns, unless force_resend=True."""
        state = get_default_state("+919876543210")
        media_config = {
            "raw_video_url": "https://example.com/videos/install.mp4",
            "how_to_create_profile_video_url": "https://youtube.com/shorts/fallback",
            "video_caption": "How to Install App",
        }

        async def run_test():
            with patch("src.services.interakt.send_video_message_async", new_callable=AsyncMock) as mock_video:
                mock_video.return_value = True

                # 1. First send: Should succeed and set state flag
                sent1 = await dispatch_media_guide_async(
                    phone="+919876543210",
                    state=state,
                    media_config=media_config,
                    state_key="app_download_video_sent",
                    force_resend=False,
                )
                self.assertTrue(sent1)
                self.assertTrue(state["app_download_video_sent"])
                self.assertEqual(mock_video.call_count, 1)

                # 2. Second send (anti-spam): Should be blocked because flag is True
                sent2 = await dispatch_media_guide_async(
                    phone="+919876543210",
                    state=state,
                    media_config=media_config,
                    state_key="app_download_video_sent",
                    force_resend=False,
                )
                self.assertFalse(sent2)
                self.assertEqual(mock_video.call_count, 1)  # Not called again!

                # 3. Third send with force_resend=True (user re-request): Should send again
                sent3 = await dispatch_media_guide_async(
                    phone="+919876543210",
                    state=state,
                    media_config=media_config,
                    state_key="app_download_video_sent",
                    force_resend=True,
                )
                self.assertTrue(sent3)
                self.assertEqual(mock_video.call_count, 2)

        asyncio.run(run_test())

    def test_dispatch_media_guide_youtube_fallback(self):
        """Verify that if raw MP4 is None or fails, it falls back to the YouTube URL text message."""
        state = get_default_state("+919876543210")
        media_config = {
            "raw_video_url": None,  # No raw video configured
            "how_to_book_video_url": "https://youtube.com/shorts/trial_fallback",
            "video_caption": "How to Book Trial",
        }

        async def run_test():
            with patch("src.core.conversation_handler.send_text_message_async", new_callable=AsyncMock) as mock_text:
                sent = await dispatch_media_guide_async(
                    phone="+919876543210",
                    state=state,
                    media_config=media_config,
                    state_key="trial_video_sent",
                    force_resend=False,
                )
                self.assertTrue(sent)
                self.assertTrue(state["trial_video_sent"])
                self.assertEqual(mock_text.call_count, 1)
                args, kwargs = mock_text.call_args
                self.assertIn("https://youtube.com/shorts/trial_fallback", args[1])

        asyncio.run(run_test())

    def test_global_rules_media_config_loaded(self):
        """Verify that global_rules.json contains the media config fields without hardcoding."""
        rules = kb_loader.get_global_rules()
        trial_cfg = rules.get("universal_course_policies", {}).get("trial_classes", {})
        self.assertIn("how_to_book_video_url", trial_cfg)
        self.assertIn("video_caption", trial_cfg)

        enroll_cfg = rules.get("universal_enrollment_flow", {})
        self.assertIn("how_to_create_profile_video_url", enroll_cfg)
        self.assertIn("video_caption", enroll_cfg)

    def test_intent_classifier_flags_contain_resend(self):
        """Verify _empty_flags contains wants_resend_video."""
        flags = _empty_flags()
        self.assertIn("wants_resend_video", flags)
        self.assertFalse(flags["wants_resend_video"])


    def test_demo_videos_preserved_when_allowed(self):
        """Verify that _ensure_app_website_links preserves YouTube demo links when allow_demo_links=True,
        while still stripping unrequested app/store download links."""
        text_with_demos_and_app_links = (
            "Yes, we have demo videos available for some of our instructors. You can check out the teaching styles of the following teachers:\n"
            "• Suman Lata: https://youtu.be/IiVVdu4NkwI?si=leLgCK40Uo5Qhr0V\n"
            "• Mradula: https://youtu.be/vXZ6UtrWpM8?si=WYpuo8Us7xIkXT8n\n"
            "• Priya Mathur: https://youtu.be/M2Zh9SaHpX4?si=RXg-HXGI5n_ftxs-\n"
            "📱 Android: https://play.google.com/store/apps/details?id=com.sensationz.sensationz.dev\n"
            "💻 Website: https://shop.sensationzperformingarts.com/"
        )

        cleaned = _ensure_app_website_links(
            text_with_demos_and_app_links,
            user_asked_for_links=False,
            allow_demo_links=True,
        )

        # Demo links MUST be preserved
        self.assertIn("https://youtu.be/IiVVdu4NkwI", cleaned)
        self.assertIn("https://youtu.be/vXZ6UtrWpM8", cleaned)
        self.assertIn("https://youtu.be/M2Zh9SaHpX4", cleaned)
        self.assertIn("Suman Lata", cleaned)
        self.assertIn("Mradula", cleaned)
        self.assertIn("Priya Mathur", cleaned)

        # App Store / Website download links MUST still be stripped
        self.assertNotIn("play.google.com", cleaned)
        self.assertNotIn("shop.sensationzperformingarts.com", cleaned)


if __name__ == "__main__":
    unittest.main()

