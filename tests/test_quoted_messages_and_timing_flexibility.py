import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
from src.api.routes import _extract_message_text
from src.core import intent_classifier
from src.core import conversation_handler


async def test_quoted_message_extraction():
    print("\n--- Test 1: Quoted/Replied message extraction ---")
    # Simulate WhatsApp Cloud API / Interakt quoted message payload
    msg_payload = {
        "text": "But this is ok",
        "context": {
            "quoted_message": {
                "body": "1 week morning and 1 week evening"
            }
        }
    }
    extracted = _extract_message_text(msg_payload)
    assert '[Replying to: "1 week morning and 1 week evening"]' in extracted
    assert "But this is ok" in extracted
    print(f"Extracted Text:\n{extracted}")
    print("PASS: Quoted message extracted and prefixed correctly!")


async def test_timing_flexibility_intent_and_response():
    print("\n--- Test 2: Timing Flexibility Query ('1 week morning and 1 week evening - But this is ok') ---")
    user_msg = '[Replying to: "1 week morning and 1 week evening"]\nBut this is ok'
    state = {
        "stage": "NEW",
        "course": "yoga",
        "welcome_sent": True,
        "is_target_ad": True
    }

    # 1. Intent classifier test
    flags = await intent_classifier.extract_intent_and_state_changes(
        text=user_msg,
        state=state,
        history=[]
    )
    print(f"Flags extracted: intent={flags.get('customer_intent')}, selected_timing={flags.get('selected_timing')}")
    assert flags.get("customer_intent") == "QUESTION", f"Expected QUESTION, got {flags.get('customer_intent')}"
    assert flags.get("selected_timing") is None, f"Expected None for selected_timing, got {flags.get('selected_timing')}"

    # 2. Conversation turn reply test
    res = await conversation_handler.handle_incoming_message(
        user_message=user_msg,
        session_state=state,
        conversation_history=[],
        course_id="yoga"
    )
    reply = res.get("reply", "")
    followup = res.get("followup", "")
    buttons = res.get("buttons", [])

    print(f"\n--- AI REPLY (Message 1) ---\n{reply}")
    print(f"\n--- AI FOLLOWUP (Message 2) ---\n{followup}")
    print(f"\n--- BUTTONS ---\n{buttons}\n")

    # Assertions
    # Must NOT blindly dump package duration list with "Great choice! For your enrollment"
    assert "for your enrollment, please select a package duration" not in reply.lower()
    # Must address flexibility / batch switching / morning & evening
    combined = (reply + " " + (followup or "")).lower()
    assert any(w in combined for w in ["switch", "change", "batch", "flexib", "dono", "morning", "evening", "attend", "join"]), \
        "Reply must directly answer the timing flexibility question"
    print("PASS: AI directly answers the batch flexibility question without blind package push!")


if __name__ == "__main__":
    asyncio.run(test_quoted_message_extraction())
    asyncio.run(test_timing_flexibility_intent_and_response())
    print("\nALL TESTS PASSED SUCCESSFULLY!")
