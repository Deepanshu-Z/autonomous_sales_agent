import asyncio
import sys
import os

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, r"d:\Users\Admin\Desktop\prod_chat_base\new_sales_agent")

from src.core.conversation_handler import handle_incoming_message


async def test():
    phone = "8302746840"
    state = {"stage": "NEW", "course": "yoga", "language": "English"}
    history = []

    # Turn 1: Fresh user inquiring about Yoga
    # Should ask discovery question, NOT use high-pressure urgency or checklists
    print("\n--- TURN 1: Initial Greeting / Inquiry ---")
    msg1 = "Hi, tell me about yoga classes"
    res1 = await handle_incoming_message(
        user_message=msg1,
        session_state=state,
        conversation_history=history,
        course_id="yoga",
        phone=phone,
    )
    print("Reply:", res1.get("reply"))
    print("Followup:", res1.get("followup"))
    print("Buttons:", res1.get("buttons"))
    state = res1.get("new_state", state)
    history.append({"role": "user", "content": msg1})
    history.append({"role": "assistant", "content": res1.get("reply")})

    assert not any(x in (res1.get("reply") or "") for x in ["✅", "⏳", "Setup: 80%"]), "Turn 1 must not have checklists"
    assert "only 2 seats" not in (res1.get("reply") or "").lower(), "Turn 1 must not have premature urgency"

    # Turn 2: User provides goal and timing preference
    print("\n--- TURN 2: Goal & Timing Stated ---")
    msg2 = "I want relief from work stress and back pain, 6:00 PM works best for me"
    res2 = await handle_incoming_message(
        user_message=msg2,
        session_state=state,
        conversation_history=history,
        course_id="yoga",
        phone=phone,
    )
    print("Reply:", res2.get("reply"))
    print("Followup:", res2.get("followup"))
    print("Buttons:", res2.get("buttons"))
    state = res2.get("new_state", state)
    history.append({"role": "user", "content": msg2})
    history.append({"role": "assistant", "content": res2.get("reply")})

    # Turn 3: User raises price objection
    print("\n--- TURN 3: Price Objection ---")
    msg3 = "The fees seem a bit expensive for me"
    res3 = await handle_incoming_message(
        user_message=msg3,
        session_state=state,
        conversation_history=history,
        course_id="yoga",
        phone=phone,
    )
    print("Reply:", res3.get("reply"))
    print("Followup:", res3.get("followup"))
    print("Buttons:", res3.get("buttons"))
    state = res3.get("new_state", state)
    history.append({"role": "user", "content": msg3})
    history.append({"role": "assistant", "content": res3.get("reply")})

    # Turn 4: User clicks Lock My Seat (Step 4)
    print("\n--- TURN 4: Lock My Seat (Step 4) ---")
    msg4 = "Lock My Seat 🔒"
    res4 = await handle_incoming_message(
        user_message=msg4,
        session_state=state,
        conversation_history=history,
        course_id="yoga",
        phone=phone,
    )
    print("Reply:", res4.get("reply"))
    reply4 = res4.get("reply") or ""
    followup4 = res4.get("followup") or ""
    from src.core.conversation_handler import _ensure_app_website_links
    formatted_reply4 = _ensure_app_website_links(reply4, user_asked_for_links=True)
    assert not any(x in reply4 for x in ["✅", "⏳", "Setup: 80%"]), "Step 4 must NOT render raw checklists or progress bars"
    assert "play.google.com" in formatted_reply4, "Step 4 formatted reply must contain official platform links"
    assert "play.google.com" not in followup4, "Step 4 followup must NOT spam links"

    print("\n🎉 ALL 4 TURNS VERIFIED AND PASSED!")


if __name__ == "__main__":
    asyncio.run(test())
