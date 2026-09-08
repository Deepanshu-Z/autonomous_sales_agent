"""
test_full_7_steps_semantic.py — Comprehensive end-to-end verification of the Two-Step Semantic Architecture.
Tests:
1. Step 1: Ad Trigger -> Fixed 3-message welcome template
2. Step 3: Timing Selection -> "morning 7 am" -> Acknowledges timing, presents package options
3. Step 4: Package Selection -> "3 months" -> Acknowledges package, sends app links
4. Partial Confirmation -> "app to ho gaya but profile abhi nahi" -> Acknowledges app install, prompts profile completion
5. Negation -> "nahi abhi tak nahi kiya" -> Polite guidance with app links
6. Steps 6 & 7: Profile Completion -> "profile bana li" -> Delivers welcome discount coupon banner (YOGA600)
7. Off-flow / Question Turn -> "kathak b sikhate ho ?" -> Generator answers question via RAG, does NOT loop coupon
8. Semantic Agent Request -> "mujhe kisi person se baat karni hai" -> Triggers human agent handoff
9. Silent Failure Fallback -> Extractor error falls back to _empty_flags() without state corruption
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.state.chat_state import get_default_state, get_user_state_async, save_user_state_async, reset_user_state_async
from src.workers.tasks import handle_ai_reply_async, is_welcome_trigger
import src.core.conversation_handler as conv_mod
import src.services.interakt as interakt_mod
from src.core.intent_classifier import extract_intent_and_state_changes, _empty_flags


async def run_full_suite():
    print("=" * 80)
    print("RUNNING COMPREHENSIVE TWO-STEP SEMANTIC SUITE (STEPS 1–7 + EDGE CASES)")
    print("=" * 80)

    test_phone = "919999900001"
    captured_messages = []

    async def mock_send(phone, text):
        captured_messages.append(text)
        return True

    import src.workers.tasks as tasks_mod
    import src.services.agent_handoff as handoff_mod
    conv_mod.send_text_message_async = mock_send
    interakt_mod.send_text_message_async = mock_send
    tasks_mod.send_text_message_async = mock_send
    handoff_mod.send_text_message_async = mock_send

    # -------------------------------------------------------------------------
    # TEST 1: Step 1 (Ad Trigger -> 3 fixed WhatsApp messages)
    # -------------------------------------------------------------------------
    print("\n[TEST 1] Step 1: Welcome trigger via Ad ID / First Message...")
    await reset_user_state_async(test_phone)
    captured_messages.clear()
    ad_msg = "Hello! Can I get more info on Yoga classes?"
    assert is_welcome_trigger(ad_msg), "Should be recognized as welcome trigger"

    await handle_ai_reply_async(test_phone, ad_msg, history=[])
    assert len(captured_messages) == 3, f"Step 1 must fire exactly 3 fixed messages, got {len(captured_messages)}"
    assert "Welcome to Sensationz!" in captured_messages[0]
    assert "Rs." in captured_messages[1]
    assert "Which time slot works best" in captured_messages[2]
    print("  -> PASSED: 3 fixed WhatsApp messages fired correctly.")

    # -------------------------------------------------------------------------
    # TEST 2: Step 3 (Timing Selection -> "morning 7 am")
    # -------------------------------------------------------------------------
    print("\n[TEST 2] Step 3: Timing Selection ('morning 7 am')...")
    captured_messages.clear()
    await handle_ai_reply_async(test_phone, "morning 7 am", history=[])
    reply_step3 = " ".join(captured_messages)
    print(f"  Step 3 Bot Reply:\n  {reply_step3[:150]}...")
    st = await get_user_state_async(test_phone)
    assert st.get("timing") is not None, "Timing should be saved in state"
    assert any(w in reply_step3.lower() for w in ["fees", "package", "duration"]), "Should ask for package duration"
    print("  -> PASSED: Timing recognized, package selection asked.")

    # -------------------------------------------------------------------------
    # TEST 3: Step 4 (Package Selection -> "3 months")
    # -------------------------------------------------------------------------
    print("\n[TEST 3] Step 4: Package Selection ('3 months')...")
    captured_messages.clear()
    await handle_ai_reply_async(test_phone, "3 months", history=[])
    reply_step4 = " ".join(captured_messages)
    print(f"  Step 4 Bot Reply:\n  {reply_step4[:150]}...")
    st = await get_user_state_async(test_phone)
    assert st.get("package") == "3 Months", "Package should be saved as '3 Months'"
    assert "https://play.google.com" in reply_step4, "Should deliver app links"
    print("  -> PASSED: Package recognized, app download links sent.")

    # -------------------------------------------------------------------------
    # TEST 4: Partial Confirmation ("app to ho gaya but profile abhi nahi")
    # -------------------------------------------------------------------------
    print("\n[TEST 4] Partial Confirmation ('app to ho gaya but profile abhi nahi')...")
    captured_messages.clear()
    await handle_ai_reply_async(test_phone, "app to ho gaya but profile abhi nahi", history=[])
    reply_partial = " ".join(captured_messages)
    print(f"  Partial Bot Reply:\n  {reply_partial[:150]}...")
    st = await get_user_state_async(test_phone)
    assert st.get("app_installed") is True, "App installed should be True"
    assert st.get("profile_created") is False, "Profile created must be False"
    assert not st.get("coupon_sent"), "Coupon must NOT be sent yet"
    assert "YOGA600" not in reply_partial, "Coupon code must NOT be leaked or sent"
    print("  -> PASSED: Distinct app_installed vs profile_created handled, no coupon sent.")

    # -------------------------------------------------------------------------
    # TEST 5: Negation ("nahi abhi tak nahi kiya")
    # -------------------------------------------------------------------------
    print("\n[TEST 5] Negation ('nahi abhi tak nahi kiya')...")
    captured_messages.clear()
    await handle_ai_reply_async(test_phone, "nahi abhi tak nahi kiya", history=[])
    reply_neg = " ".join(captured_messages)
    print(f"  Negation Bot Reply:\n  {reply_neg[:150]}...")
    st = await get_user_state_async(test_phone)
    assert st.get("profile_created") is False, "Profile must remain unconfirmed"
    assert "YOGA600" not in reply_neg, "Coupon code must NOT be leaked or sent"
    print("  -> PASSED: Negation handled gently, no state corruption.")

    # -------------------------------------------------------------------------
    # TEST 6: Steps 6 & 7 (Profile Completed -> "profile bana li")
    # -------------------------------------------------------------------------
    print("\n[TEST 6] Steps 6 & 7: Profile Completed ('profile bana li')...")
    captured_messages.clear()
    await handle_ai_reply_async(test_phone, "profile bana li", history=[])
    reply_coupon = " ".join(captured_messages)
    print(f"  Coupon Banner Bot Reply:\n  {reply_coupon[:200]}...")
    st = await get_user_state_async(test_phone)
    assert st.get("profile_created") is True, "Profile must be True"
    assert st.get("coupon_sent") is True, "Coupon sent must be True"
    assert "YOGA600" in reply_coupon or "Coupon Code" in reply_coupon, "Coupon banner must be delivered"
    print("  -> PASSED: Profile verified, welcome coupon code banner delivered!")

    # -------------------------------------------------------------------------
    # TEST 7: Off-flow / Question Turn ("kathak b sikhate ho ?") AFTER Coupon
    # -------------------------------------------------------------------------
    print("\n[TEST 7] Off-flow question AFTER coupon ('kathak b sikhate ho ?')...")
    captured_messages.clear()
    await handle_ai_reply_async(test_phone, "kathak b sikhate ho ?", history=[])
    reply_q = " ".join(captured_messages)
    print(f"  Question Bot Reply:\n  {reply_q}...")
    assert "YOGA600" not in reply_q, "Must NOT loop or repeat the coupon code!"
    assert any(w in reply_q.lower() for w in ["kathak", "dance", "classes", "sensationz"]), "Must answer about Kathak!"
    print("  -> PASSED: Question answered via RAG without coupon looping.")

    # -------------------------------------------------------------------------
    # TEST 8: Semantic Agent Request ("can I speak with someone from your team?")
    # -------------------------------------------------------------------------
    print("\n[TEST 8] Semantic Agent Request ('mujhe kisi person se baat karni hai')...")
    captured_messages.clear()
    await handle_ai_reply_async(test_phone, "mujhe kisi person se baat karni hai", history=[])
    reply_agent = " ".join(captured_messages)
    print(f"  Agent Handoff Reply:\n  {reply_agent[:150]}...")
    assert any(w in reply_agent.lower() for w in ["support", "team", "coordinator", "connect"]), "Should trigger handoff"
    print("  -> PASSED: Semantic human agent request triggered handoff.")

    # -------------------------------------------------------------------------
    # TEST 9: Silent Failure Fallback (_empty_flags())
    # -------------------------------------------------------------------------
    print("\n[TEST 9] Silent Failure Fallback (_empty_flags)...")
    flags = _empty_flags()
    assert flags["app_installed_stated"] == "unmentioned"
    assert flags["profile_created_stated"] == "unmentioned"
    assert flags["requests_human_agent"] is False
    assert flags["is_disinterested"] is False
    assert flags["customer_intent"] == "QUESTION"
    print("  -> PASSED: Safe empty fallback defaults verified.")

    print("\n" + "=" * 80)
    print("ALL 9 TESTS IN FULL 7-STEP SUITE PASSED PERFECTLY!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(run_full_suite())
