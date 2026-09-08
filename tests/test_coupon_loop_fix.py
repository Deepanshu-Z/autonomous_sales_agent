"""
test_coupon_loop_fix.py — Verify that questions and greetings do not trigger repeat coupons.
"""

import sys
import os
import asyncio
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.state.chat_state import is_profile_completed_signal, get_default_state
from src.core.intent_classifier import is_app_link_request

def test_profile_completed_signal():
    print("Testing is_profile_completed_signal...")

    state = get_default_state("919876543210")
    state["stage"] = "APP_LINK_SENT"
    state["coupon_sent"] = True  # Coupon was already sent previously!

    # 1. Non-completion messages must NOT signal profile completion
    assert not is_profile_completed_signal("hi", state), "'hi' should not be profile completion"
    assert not is_profile_completed_signal("reset", state), "'reset' should not be profile completion"
    assert not is_profile_completed_signal("kathak b sikhate ho ?", state), "'kathak b sikhate ho ?' should not be profile completion"
    assert not is_profile_completed_signal("ok", state), "'ok' when coupon already sent should not be profile completion"

    # 2. When coupon NOT sent yet:
    fresh_state = get_default_state("919876543210")
    fresh_state["stage"] = "APP_LINK_SENT"
    fresh_state["coupon_sent"] = False

    assert not is_profile_completed_signal("hi", fresh_state), "'hi' should not be profile completion on fresh state"
    assert not is_profile_completed_signal("kathak b sikhate ho ?", fresh_state), "Question should not be profile completion"
    assert is_profile_completed_signal("profile bana li", fresh_state), "'profile bana li' should signal profile completion"
    assert is_profile_completed_signal("done", fresh_state), "'done' should signal profile completion in APP_LINK_SENT stage"

    print("All is_profile_completed_signal tests passed!")

async def test_full_reply_flow():
    print("\nTesting full handle_ai_reply_async flow...")
    import src.workers.tasks as tasks_mod
    from src.state.chat_state import save_user_state_async

    test_phone = "919876543299"
    captured_replies = []

    async def mock_send(phone, msg):
        captured_replies.append(msg)
        return True

    tasks_mod.send_text_message_async = mock_send
    import src.core.conversation_handler as conv_mod
    import src.services.interakt as interakt_mod
    conv_mod.send_text_message_async = mock_send
    interakt_mod.send_text_message_async = mock_send

    # Scenario 1: User already received coupon previously
    state = get_default_state(test_phone)
    state["stage"] = "COUPON_SENT"
    state["coupon_sent"] = True
    state["profile_created"] = True
    state["is_target_ad"] = True
    await save_user_state_async(test_phone, state)

    # 1. Ask about Kathak classes
    captured_replies.clear()
    await tasks_mod.handle_ai_reply_async(test_phone, "kathak b sikhate ho ?", history=[])
    full_text = " ".join(captured_replies)
    print(f"\n[Reply to 'kathak b sikhate ho ?']:\n{full_text}\n")

    assert "Coupon Code: YOGA600" not in full_text, "Should NOT send coupon banner to a question!"
    assert any(w in full_text.lower() for w in ["kathak", "dance", "sensationz"]), "Should answer about Kathak!"

    # 2. Say 'reset'
    captured_replies.clear()
    await tasks_mod.handle_ai_reply_async(test_phone, "reset", history=[])
    full_reset = " ".join(captured_replies)
    print(f"\n[Reply to 'reset']:\n{full_reset}\n")
    assert "reset" in full_reset.lower(), "Should execute reset command!"

    print("All full reply flow tests passed successfully!")

if __name__ == "__main__":
    test_profile_completed_signal()
    asyncio.run(test_full_reply_flow())
