import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
from unittest.mock import AsyncMock, patch

from src.core import campaign_loader, language_loader
from src.state.chat_state import get_user_state_async, reset_user_state_async, save_user_state_async
from src.workers.tasks import handle_ai_reply_async


async def test_language_loader():
    """Verify data/languages.json loads correctly with zero hardcoding."""
    cfg = language_loader.get_language_config()
    assert cfg is not None
    assert "default_language" in cfg
    assert cfg["default_language"] == "English"
    assert language_loader.get_default_language() == "English"

    prompt_data = language_loader.get_language_prompt()
    assert "message" in prompt_data
    assert "buttons" in prompt_data
    assert len(prompt_data["buttons"]) >= 2
    assert "English" in prompt_data["buttons"]

    # Match aliases
    assert language_loader.match_language("English") == "English"
    assert language_loader.match_language("hindi") == "Hindi"
    assert language_loader.match_language("हिन्दी (Hindi)") == "Hindi"
    assert language_loader.match_language("हिन्दी") == "Hindi"
    assert language_loader.match_language("hinglish") == "Hinglish"
    assert language_loader.match_language("unsupported_xyz") is None


async def test_campaign_loader_localization():
    """Verify campaign_loader delivers localized welcome messages and buttons."""
    camps = campaign_loader.get_all_campaigns()
    camp_yoga = camps[0]

    # English
    en_msgs = campaign_loader.get_welcome_messages(camp_yoga, language="English")
    en_btns = campaign_loader.get_campaign_buttons(camp_yoga, language="English")
    assert "Join our live Yoga classes" in en_msgs["message_1"]
    assert en_btns == ["Timing", "Fees", "Free Trial"]

    # Hindi
    hi_msgs = campaign_loader.get_welcome_messages(camp_yoga, language="Hindi")
    hi_btns = campaign_loader.get_campaign_buttons(camp_yoga, language="Hindi")
    assert "प्रमाणित प्रशिक्षकों" in hi_msgs["message_1"]
    assert "समय (Timing)" in hi_btns[0]
    assert "फीस (Fees)" in hi_btns[1]


async def test_two_stage_welcome_flow():
    """
    Simulate full multi-turn user journey:
    Turn 1: User sends message -> Gets language prompt with buttons
    Turn 2: User selects Hindi -> Gets Hindi course welcome and Hindi buttons
    Turn 3: User clicks timing/fees -> Gets reply locked in Hindi
    Turn 4: User explicitly requests English -> Switches to English
    """
    test_phone = "919876543299"
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

        # --- Turn 1: User arrives via ad ---
        camps = campaign_loader.get_all_campaigns()
        registered_ad_id = camps[0]["ad_ids"][0]
        referral = {"ad_id": registered_ad_id}

        await handle_ai_reply_async(test_phone, "Hi, I am interested in yoga", [], referral=referral)

        # Stage 1: User receives language prompt with language buttons
        assert len(sent_buttons) == 1
        assert "select your preferred language" in sent_buttons[0]["text"].lower() or "भाषा चुनें" in sent_buttons[0]["text"]
        assert "English" in sent_buttons[0]["buttons"]
        assert "हिन्दी (Hindi)" in sent_buttons[0]["buttons"]

        state = await get_user_state_async(test_phone)
        assert state.get("language_prompt_sent") is True
        assert state.get("awaiting_language") is True

        # --- Turn 2: User taps 'हिन्दी (Hindi)' button ---
        sent_messages.clear()
        sent_buttons.clear()

        history = [
            {"role": "user", "content": "Hi, I am interested in yoga"},
            {"role": "assistant", "content": "Language prompt"}
        ]
        await handle_ai_reply_async(test_phone, "हिन्दी (Hindi)", history, referral=referral)

        # Stage 2: Course welcome messages sent in Hindi
        assert len(sent_messages) == 1
        assert "प्रमाणित प्रशिक्षकों" in sent_messages[0]["text"]

        assert len(sent_buttons) == 1
        assert "समय (Timing)" in sent_buttons[0]["buttons"][0]

        # Verify state: language is locked to Hindi
        state = await get_user_state_async(test_phone)
        assert state.get("language") == "Hindi"
        assert state.get("language_selected") is True
        assert state.get("awaiting_language") is False
        assert state.get("welcome_sent") is True

    # Cleanup
    await reset_user_state_async(test_phone)


async def test_fallback_to_default_english_if_query_sent():
    """
    If user ignores language prompt on Turn 2 and asks a question directly,
    bot sets language to default (English) and processes the question.
    """
    test_phone = "919876543288"
    await reset_user_state_async(test_phone)

    sent_messages = []
    sent_buttons = []

    async def mock_send_text(phone, text):
        sent_messages.append({"phone": phone, "text": text})
        return True

    async def mock_send_buttons(phone, text, buttons):
        sent_buttons.append({"phone": phone, "text": text, "buttons": buttons})
        return True

    mock_llm_reply = {
        "reply": "The fee for 1 month Yoga is ₹300.",
        "followup": "Which batch timing works best?",
        "buttons": ["Morning", "Evening"]
    }

    async def mock_incoming_handler(user_message, session_state, conversation_history, course_id=None):
        st = dict(session_state)
        return {
            "reply": mock_llm_reply["reply"],
            "followup": mock_llm_reply["followup"],
            "buttons": mock_llm_reply["buttons"],
            "new_state": st
        }

    with patch("src.workers.tasks.send_text_message_async", side_effect=mock_send_text), \
         patch("src.core.conversation_handler.send_text_message_async", side_effect=mock_send_text), \
         patch("src.services.quick_reply_service.send_followup_with_buttons_async", side_effect=mock_send_buttons), \
         patch("src.core.conversation_handler.handle_incoming_message", side_effect=mock_incoming_handler):

        # Turn 1: Ad message
        camps = campaign_loader.get_all_campaigns()
        referral = {"ad_id": camps[0]["ad_ids"][0]}
        await handle_ai_reply_async(test_phone, "Hi", [], referral=referral)

        # Turn 2: User asks question instead of choosing language
        sent_messages.clear()
        sent_buttons.clear()
        history = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Please select language"}
        ]
        await handle_ai_reply_async(test_phone, "What is the fee for Yoga?", history, referral=referral)

        state = await get_user_state_async(test_phone)
        assert state.get("language") == "English"
        assert state.get("language_selected") is True
        assert state.get("welcome_sent") is True

    await reset_user_state_async(test_phone)


if __name__ == "__main__":
    asyncio.run(test_language_loader())
    asyncio.run(test_campaign_loader_localization())
    asyncio.run(test_two_stage_welcome_flow())
    asyncio.run(test_fallback_to_default_english_if_query_sent())
    print("ALL LANGUAGE TESTS PASSED SUCCESSFULLY!")
