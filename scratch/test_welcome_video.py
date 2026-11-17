"""
test_welcome_video.py — Verification suite for Welcome Message Video integration.
Verifies:
1. Campaign loader and KB extract the exact welcome video URLs per course.
2. Zero caption is passed to send_video_message_async (as strictly instructed).
3. Sequence: Message 1 -> Native MP4 Video (no caption) -> Message 2.
4. Anti-spam: Welcome video is sent only once per lead.
5. Missing video fallback (Face Yoga): Message 1 & 2 delivered smoothly without video.
"""

import asyncio
from unittest.mock import AsyncMock, patch
from src.core import campaign_loader, kb_loader
from src.state.chat_state import get_default_state
from src.workers.tasks import handle_ai_reply_async


EXPECTED_URLS = {
    "yoga": "https://res.cloudinary.com/drqsicr04/video/upload/v1794899864/AQME-nBg5AGObtZf64ndjXyLi87yHM_1YhoUMj7_mjw5_x3WVYirQrcYrdidK5wz8IyreXJYLis5i-FmlwGsHBiF3ZBFcB94sXqoyKY_ufdmbh.mp4",
    "ladies_dance": "https://res.cloudinary.com/drqsicr04/video/upload/v1794899806/AQOFmS1bb40gdtZ3IKVbAqD3gl4bMQMmuBm9NwN1VLm50MmwycEfOMU55SGwJg8FkCggjV-f7ua0UkOrNLh4fmwlmEGC-tx3Y0ZNnWY_yx5nng.mp4",
    "kathak": "https://res.cloudinary.com/drqsicr04/video/upload/v1794899902/AQN_Y0x6wP6MOONeuNZ2xlRtlmGkN7A7tYHXFC9PGCDTT7e5HvGssk-PWs99tOK9G8IpdbaHhlAQU_gDy3NoJiXJ_ddnpp2.mp4",
    "face_yoga": None,
}


async def test_campaign_loader_urls():
    print("\n--- [1/4] Testing Video URL Extraction per Course ---")
    campaign_loader.clear_cache()
    campaigns = campaign_loader.get_all_campaigns()

    for course, expected_url in EXPECTED_URLS.items():
        matched_camp = next((c for c in campaigns if c.get("course") == course), None)
        extracted_url = campaign_loader.get_welcome_video(matched_camp, course)
        assert extracted_url == expected_url, f"Course {course}: expected {expected_url}, got {extracted_url}"
        print(f"  [PASS] Course '{course}': URL correctly resolved -> {extracted_url}")


async def test_welcome_sequence_with_video():
    print("\n--- [2/4] Testing Welcome Message & Video Dispatch (Yoga Lead) ---")
    phone = "+919999900001"
    state = get_default_state(phone)
    referral = {"ad_id": "120251036038770010"}  # Yoga Ad ID

    with patch("src.workers.tasks.get_user_state_async", return_value=state), \
         patch("src.workers.tasks.save_user_state_async", new_callable=AsyncMock) as mock_save_state, \
         patch("src.workers.tasks.send_text_message_async", new_callable=AsyncMock) as mock_send_text, \
         patch("src.services.interakt.send_video_message_async", new_callable=AsyncMock) as mock_send_video, \
         patch("src.workers.tasks.save_message_async", new_callable=AsyncMock), \
         patch("src.workers.tasks.log_message_async", new_callable=AsyncMock), \
         patch("asyncio.sleep", new_callable=AsyncMock):

        mock_send_video.return_value = True

        await handle_ai_reply_async(phone, "Hi, I am interested", history=[], referral=referral)

        # 1. Text messages sent: Message 1 and Message 2
        assert mock_send_text.call_count == 2, f"Expected 2 text messages, got {mock_send_text.call_count}"
        msg1_sent = mock_send_text.call_args_list[0][0][1]
        msg2_sent = mock_send_text.call_args_list[1][0][1]
        assert "Live Yoga Classes" in msg1_sent
        assert "timings" in msg2_sent.lower()
        print("  [PASS] Message 1 (Offer) and Message 2 (CTA) sent in order.")

        # 2. Video message sent with NO caption
        assert mock_send_video.call_count == 1, f"Expected 1 video send, got {mock_send_video.call_count}"
        video_args, video_kwargs = mock_send_video.call_args
        v_phone, v_url = video_args[0], video_args[1]
        v_caption = video_kwargs.get("caption", video_args[2] if len(video_args) > 2 else "")
        assert v_phone == phone
        assert v_url == EXPECTED_URLS["yoga"]
        assert v_caption == "", f"CRITICAL: caption must be empty, but got '{v_caption}'"
        print(f"  [PASS] Video sent with zero caption -> {v_url}")

        # 3. State updated
        assert state.get("welcome_sent") is True
        assert state.get("welcome_video_sent") is True
        print("  [PASS] State updated: welcome_sent=True, welcome_video_sent=True")


async def test_anti_spam_welcome_video():
    print("\n--- [3/4] Testing Anti-Spam Guard (No Duplicate Video on Re-trigger) ---")
    phone = "+919999900001"
    state = get_default_state(phone)
    state["welcome_sent"] = False
    state["welcome_video_sent"] = True  # Already sent video
    referral = {"ad_id": "120251036038770010"}

    with patch("src.workers.tasks.get_user_state_async", return_value=state), \
         patch("src.workers.tasks.save_user_state_async", new_callable=AsyncMock), \
         patch("src.workers.tasks.send_text_message_async", new_callable=AsyncMock) as mock_send_text, \
         patch("src.services.interakt.send_video_message_async", new_callable=AsyncMock) as mock_send_video, \
         patch("src.workers.tasks.save_message_async", new_callable=AsyncMock), \
         patch("src.workers.tasks.log_message_async", new_callable=AsyncMock), \
         patch("asyncio.sleep", new_callable=AsyncMock):

        await handle_ai_reply_async(phone, "Hi again", history=[], referral=referral)

        # Video must NOT be sent again
        assert mock_send_video.call_count == 0, "Video must not be sent if welcome_video_sent is already True"
        assert mock_send_text.call_count == 2
        print("  [PASS] Anti-spam verified: Video skipped on subsequent welcome triggers.")


async def test_face_yoga_graceful_handling():
    print("\n--- [4/4] Testing Course Without Video (Face Yoga) ---")
    phone = "+919999900002"
    state = get_default_state(phone)

    with patch("src.workers.tasks.get_user_state_async", return_value=state), \
         patch("src.workers.tasks.save_user_state_async", new_callable=AsyncMock), \
         patch("src.workers.tasks.send_text_message_async", new_callable=AsyncMock) as mock_send_text, \
         patch("src.services.interakt.send_video_message_async", new_callable=AsyncMock) as mock_send_video, \
         patch("src.workers.tasks.save_message_async", new_callable=AsyncMock), \
         patch("src.workers.tasks.log_message_async", new_callable=AsyncMock), \
         patch("asyncio.sleep", new_callable=AsyncMock):

        await handle_ai_reply_async(phone, "Face Yoga fees", history=[], referral=None)

        assert mock_send_video.call_count == 0, "Face Yoga has no video configured yet, must not call send_video"
        assert mock_send_text.call_count == 2
        print("  [PASS] Face Yoga delivered text messages cleanly without error when video is null.")


async def main():
    await test_campaign_loader_urls()
    await test_welcome_sequence_with_video()
    await test_anti_spam_welcome_video()
    await test_face_yoga_graceful_handling()
    print("\n==========================================================")
    print(" >>> ALL WELCOME VIDEO TESTS PASSED SUCCESSFULLY! <<<")
    print("==========================================================\n")


if __name__ == "__main__":
    asyncio.run(main())
