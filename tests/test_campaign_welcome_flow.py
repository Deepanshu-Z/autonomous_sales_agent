import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
from unittest.mock import AsyncMock, patch

from src.core import campaign_loader
from src.state.chat_state import get_user_state_async, reset_user_state_async, save_user_state_async
from src.workers.tasks import handle_ai_reply_async, check_target_ad


async def test_campaign_matching():
    camps = campaign_loader.get_all_campaigns()
    assert len(camps) > 0
    registered_ad_id = camps[0]["ad_ids"][0]

    camp_yoga = campaign_loader.match_campaign(referral={"ad_id": registered_ad_id})
    assert camp_yoga is not None
    assert camp_yoga["course"] == "yoga"
    assert len(camp_yoga["buttons"]) == 3
    assert camp_yoga["buttons"] == ["Timing", "Fees", "Other questions"]

    # 2. Unregistered Ad ID strictly returns None (ignored)
    assert campaign_loader.match_campaign(referral={"ad_id": "999999999999999999"}) is None

    # 3. Direct testing without referral (e.g. whitelisted number direct message)
    # Specified course
    camp_direct_kathak = campaign_loader.match_campaign(referral=None, text="Hi, tell me about Kathak")
    assert camp_direct_kathak is not None
    assert camp_direct_kathak["course"] == "kathak"

    # Default course
    camp_direct_default = campaign_loader.match_campaign(referral=None, text="Hi")
    assert camp_direct_default is not None
    assert camp_direct_default["course"] == "yoga"


async def test_registered_ad_id_check():
    camps = campaign_loader.get_all_campaigns()
    registered_ad_id = camps[0]["ad_ids"][0]

    # Registered in ad_campaigns.json
    assert campaign_loader.is_registered_ad_id(registered_ad_id) is True
    # Non-registered
    assert campaign_loader.is_registered_ad_id("999999999999999999") is False

    # Check check_target_ad integration
    is_target, reason = check_target_ad("Hi", {"ad_id": registered_ad_id}, "919999999999")
    assert is_target is True


async def test_first_message_welcome_and_second_message_funnel():
    test_phone = "919876543210"
    await reset_user_state_async(test_phone)

    sent_messages = []
    sent_buttons = []

    async def mock_send_text(phone, text):
        sent_messages.append({"phone": phone, "text": text})
        return True

    async def mock_send_buttons(phone, text, buttons):
        sent_buttons.append({"phone": phone, "text": text, "buttons": buttons})
        return True

    with patch("src.workers.tasks.send_text_message_async", side_effect=mock_send_text), \
         patch("src.core.conversation_handler.send_text_message_async", side_effect=mock_send_text), \
         patch("src.services.quick_reply_service.send_followup_with_buttons_async", side_effect=mock_send_buttons):

        camps = campaign_loader.get_all_campaigns()
        registered_ad_id = camps[0]["ad_ids"][0]
        referral = {"ad_id": registered_ad_id}
        await handle_ai_reply_async(test_phone, "Hi, I am interested in classes", [], referral=referral)

        # Verify Welcome message 1 sent as text
        assert len(sent_messages) == 1
        assert "Welcome to Sensationz" in sent_messages[0]["text"]
        assert "Live Interactive Yoga classes" in sent_messages[0]["text"]

        # Verify Welcome message 2 sent with 3 interactive buttons
        assert len(sent_buttons) == 1
        assert "get started today" in sent_buttons[0]["text"]
        assert sent_buttons[0]["buttons"] == ["Timing", "Fees", "Other questions"]

        # Verify state populated
        state = await get_user_state_async(test_phone)
        assert state.get("welcome_sent") is True
        assert state.get("course") == "yoga"

        # --- Turn 2: Customer clicks button "Fees" ---
        sent_messages.clear()
        sent_buttons.clear()

        # Simulate customer answering
        history = [
            {"role": "user", "content": "Hi, I am interested in classes"},
            {"role": "assistant", "content": sent_messages[0]["text"] if sent_messages else "welcome"}
        ]
        await handle_ai_reply_async(test_phone, "Fees", history, referral=referral)

        # Welcome flow should NOT trigger again!
        assert len(sent_buttons) == 0 or not any("Which details would you like to check first?" in b.get("text", "") for b in sent_buttons)
        # Main reply must have been sent for yoga fees
        assert len(sent_messages) >= 1
        combined_text = " ".join(m["text"] for m in sent_messages).lower()
        assert "₹" in combined_text or "rs" in combined_text or "fees" in combined_text or "package" in combined_text

    # Cleanup
    await reset_user_state_async(test_phone)


if __name__ == "__main__":
    asyncio.run(test_campaign_matching())
    asyncio.run(test_registered_ad_id_check())
    asyncio.run(test_first_message_welcome_and_second_message_funnel())
    print("ALL TESTS PASSED SUCCESSFULLY!")
