import sys
import os
sys.path.insert(0, os.path.abspath("."))
import asyncio
from unittest.mock import patch, AsyncMock
from dotenv import load_dotenv

load_dotenv()

from src.core.conversation_handler import _handle_conversation_turn_unlocked

async def run_test():
    phone = "919999999999"
    state = {"course": "kathak", "stage": "NEW"}

    with patch("src.core.conversation_handler.get_user_state_async", AsyncMock(return_value=state)), \
         patch("src.core.conversation_handler.save_user_state_async", AsyncMock()), \
         patch("src.core.conversation_handler.send_text_message_async", AsyncMock(return_value=True)) as mock_send_text, \
         patch("src.services.interakt.send_video_message_async", AsyncMock(return_value=True)) as mock_send_video, \
         patch("src.core.conversation_handler.save_message_async", AsyncMock()):

        print("\n--- TURN 1: 'can u send me class video' ---")
        res1 = await _handle_conversation_turn_unlocked(phone, "can u send me class video", history=[])
        print("Reply 1:", res1.get("reply")[:100] if res1.get("reply") else None)
        print("send_video called count:", mock_send_video.call_count)
        if mock_send_video.call_args:
            print("send_video args:", mock_send_video.call_args)

        mock_send_video.reset_mock()
        mock_send_text.reset_mock()

        # Simulate state after Turn 1
        state["class_demo_video_sent"] = True

        print("\n--- TURN 2: 'send videos again' ---")
        history = [
            {"role": "user", "content": "can u send me class video"},
            {"role": "assistant", "content": res1.get("reply") or ""}
        ]
        res2 = await _handle_conversation_turn_unlocked(phone, "send videos again", history=history)
        print("Reply 2:", res2.get("reply")[:100] if res2.get("reply") else None)
        print("send_video called count:", mock_send_video.call_count)
        if mock_send_video.call_args:
            print("send_video args:", mock_send_video.call_args)

if __name__ == "__main__":
    asyncio.run(run_test())
