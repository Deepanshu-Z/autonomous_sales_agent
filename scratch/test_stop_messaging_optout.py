"""
scratch/test_stop_messaging_optout.py — Verification of Stop Messaging / Irritation Opt-Out & Support Team.

Tests:
1. Customer Support Executives order and salutations: Rachna, Sudesh, Monika, Ruchi.
2. Semantic Intent Classification: extracts wants_stop_messaging=True across diverse phrasings.
3. Refusal Engine handling: does not pitch solutions or ask objection questions.
4. End-to-End Turn: polite opt-out response, support numbers listed, follow-ups disarmed, bot halted.
"""

import asyncio
import os
import sys

# Add project root to path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.core import kb_loader
from src.core.intent_classifier import extract_intent_and_state_changes
from src.core.refusal_engine import evaluate_refusal_async
from src.core.conversation_handler import handle_incoming_message


def test_1_support_executives():
    print("\n--- [1/4] Testing Customer Support Executives Order & Salutations ---")
    execs_display = kb_loader.format_support_executives_display()
    print("Formatted Display:\n" + execs_display)

    lines = execs_display.strip().split("\n")
    assert len(lines) == 4, f"Expected 4 executives, got {len(lines)}"

    assert "Rachna ma'am: +91 88260 57446" in lines[0], f"Line 0 mismatch: {lines[0]}"
    assert "Sudesh sir: +91 88105 82411" in lines[1], f"Line 1 mismatch: {lines[1]}"
    assert "Monika ma'am: +91 93544 05863" in lines[2], f"Line 2 mismatch: {lines[2]}"
    assert "Ruchi ma'am: +91 88003 48485" in lines[3], f"Line 3 mismatch: {lines[3]}"
    assert "Pooja" not in execs_display, "Pooja should not be in the support executives list!"
    print("  [PASS] Support executives order, phone numbers, and salutations match 100%!")


async def test_2_semantic_classification():
    print("\n--- [2/4] Testing Semantic Intent Classification on Diverse Stop Phrasings ---")
    test_phrases = [
        "stop sending msg",
        "pls don't text me on whatsapp",
        "bar bar msg kyu bhej rahe ho band karo",
        "dimag kharab mat karo msg mat bhejna dobara",
    ]

    for phrase in test_phrases:
        flags = await extract_intent_and_state_changes(phrase, state={"course": "yoga"})
        wants_stop = flags.get("wants_stop_messaging")
        intent = flags.get("customer_intent")
        is_disint = flags.get("is_disinterested")

        print(f"Input: '{phrase}'")
        print(f"  -> wants_stop_messaging: {wants_stop}, intent: {intent}, is_disinterested: {is_disint}")

        assert wants_stop is True, f"Expected wants_stop_messaging=True for '{phrase}', got {wants_stop}"
        assert intent == "STOP_MESSAGING", f"Expected intent='STOP_MESSAGING' for '{phrase}', got {intent}"
        assert is_disint is True, f"Expected is_disinterested=True for '{phrase}', got {is_disint}"
    print("  [PASS] All diverse stop messaging phrasings correctly classified!")


async def test_3_refusal_engine():
    print("\n--- [3/4] Testing Refusal Engine Guard for Stop Messaging ---")
    res = await evaluate_refusal_async("stop sending messages please", state={"course": "yoga"})
    print(f"Refusal Engine Result: resolution_type={res.get('resolution_type')}, solution_viable={res.get('solution_viable')}")
    assert res.get("solution_viable") is False, "Stop messaging cannot be marked as solvable via courses!"
    assert res.get("resolution_type") != "ASK_REASON", "Must not ask why they want to stop!"
    print("  [PASS] Refusal engine does not badger customer or ask why!")


async def test_4_e2e_turn():
    print("\n--- [4/4] Testing End-to-End Chat Turn on Irritated Stop Request ---")
    session_state = {
        "phone": "9999900002",
        "course": "yoga",
        "stage": "NEW",
        "next_followup_due_at": 1731600000,
        "is_escalated": False,
    }
    history = [
        {"role": "user", "content": "Yoga classes fees?"},
        {"role": "assistant", "content": "Yoga classes 1 month ka offer fee Rs 1750 hai."},
    ]
    user_msg = "Mujhe koi msg mat bhejo ab se, bilkul irritate mat karo"

    print(f"Customer Input: '{user_msg}'")
    res = await handle_incoming_message(
        user_message=user_msg,
        session_state=session_state,
        conversation_history=history,
        course_id="yoga",
    )

    reply = res.get("reply", "")
    followup = res.get("followup")
    new_state = res.get("new_state", {})

    print(f"\nAI Reply:\n{reply}")
    print(f"\nAI Followup: {followup}")
    print(f"is_escalated: {new_state.get('is_escalated')}")
    print(f"next_followup_due_at: {new_state.get('next_followup_due_at')}")

    assert followup is None, f"Followup must be None on opt-out, got: {followup}"
    assert "automated messages" in reply.lower() or "automated" in reply.lower() or "hata diya" in reply.lower(), "Reply must confirm removal from messages!"
    assert "Rachna ma'am: +91 88260 57446" in reply, "Must include Rachna ma'am contact!"
    assert "Sudesh sir: +91 88105 82411" in reply, "Must include Sudesh sir contact!"
    assert "Monika ma'am: +91 93544 05863" in reply, "Must include Monika ma'am contact!"
    assert "Ruchi ma'am: +91 88003 48485" in reply, "Must include Ruchi ma'am contact!"
    assert new_state.get("is_escalated") is True, "Customer must be marked escalated!"
    assert new_state.get("next_followup_due_at") is None, "Follow-up timers must be cleared to None!"
    print("  [PASS] End-to-end turn delivered polite acknowledgment with support contacts and disarmed system!")


async def main():
    test_1_support_executives()
    await test_2_semantic_classification()
    await test_3_refusal_engine()
    await test_4_e2e_turn()
    print("\n==========================================================")
    print(" >>> ALL STOP MESSAGING & SUPPORT TEAM TESTS PASSED! <<<")
    print("==========================================================")


if __name__ == "__main__":
    asyncio.run(main())

