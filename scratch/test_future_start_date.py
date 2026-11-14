"""
scratch/test_future_start_date.py — Verification of Future Start Date & Price Lock Mandate.

Tests:
1. Semantic Extractor / Fallback correctly extracts preferred_start_date.
2. is_disinterested is strictly FALSE when customer specifies a future start date.
3. Refusal Engine evaluate_refusal_async correctly treats future dates as NOT_A_REFUSAL.
4. format_system_prompt injects preferred_start_date and Section 0f instructions.
"""

import asyncio
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.intent_classifier import extract_intent_and_state_changes, apply_extraction_to_state
from src.core.refusal_engine import evaluate_refusal_async
from src.core.prompt import format_system_prompt


async def test_extraction():
    test_cases = [
        ("Main next month se join karunga", "next month"),
        ("1st Dec se start karna hai", "1st Dec"),
        ("Agle mahine se class shuru karni hai", "Agle mahine"),
        ("Next week se join karna chahta hoon", "Next week"),
    ]

    print("\n--- 1. Testing Future Start Date Extraction & Disinterest Guard ---")
    for text, expected_substr in test_cases:
        res = await extract_intent_and_state_changes(text, state={"course": "yoga"})
        pref_date = res.get("preferred_start_date")
        is_disint = res.get("is_disinterested")

        print(f"Input: '{text}'")
        print(f"  -> Extracted preferred_start_date: '{pref_date}'")
        print(f"  -> is_disinterested: {is_disint}")

        assert pref_date is not None, f"Expected preferred_start_date to be extracted for '{text}'"
        assert is_disint is False, f"Expected is_disinterested=False for '{text}', got {is_disint}"
        assert expected_substr.lower() in pref_date.lower(), f"Expected '{expected_substr}' in '{pref_date}'"

        # Test apply_extraction_to_state
        state = {"stage": "NEW", "course": "yoga"}
        new_state = apply_extraction_to_state(state, res)
        assert new_state.get("preferred_start_date") == pref_date
        assert new_state.get("_last_turn", {}).get("preferred_start_date") == pref_date
    print("PASS: All extraction cases passed!")


async def test_refusal_engine_guard():
    print("\n--- 2. Testing Refusal Engine Guard for Future Dates ---")
    test_refusals = [
        "Main next month se start karunga abhi nahi",
        "1st dec se join karna hai",
        "Agle mahine se shuru karenge",
        "Next week se class attend karungi",
    ]

    for text in test_refusals:
        eval_res = await evaluate_refusal_async(text, state={"course": "yoga", "refusal_stage": 1})
        is_refusal = eval_res.get("is_refusal")
        res_type = eval_res.get("resolution_type")
        print(f"Input: '{text}'")
        print(f"  -> is_refusal: {is_refusal}")
        print(f"  -> resolution_type: {res_type}")

        assert is_refusal is False, f"Expected is_refusal=False for '{text}', got {is_refusal}"
        assert res_type == "NOT_A_REFUSAL", f"Expected NOT_A_REFUSAL for '{text}', got {res_type}"
    print("PASS: Refusal engine guards passed!")


def test_prompt_injection():
    print("\n--- 3. Testing System Prompt Injection for Future Start Date ---")
    sample_state = {
        "course": "yoga",
        "preferred_start_date": "1st December",
        "timing": "7:00 AM to 8:00 AM",
        "stage": "TIMING_SELECTED"
    }

    prompt = format_system_prompt(state=sample_state)
    assert "0f. FUTURE START DATE & PRICE LOCK MANDATE" in prompt, "Section 0f missing from system prompt!"
    assert "CUSTOMER PREFERRED FUTURE START DATE: '1st December'" in prompt, "preferred_start_date not injected into context!"
    assert "lock in their package and batch slot today at the current discounted offer price" in prompt, "Price lock pitch missing!"
    print("PASS: System prompt contains Section 0f and preferred_start_date context injection!")


async def main():
    await test_extraction()
    await test_refusal_engine_guard()
    test_prompt_injection()
    print("\nALL FUTURE START DATE & PRICE LOCK VERIFICATIONS PASSED SUCCESSFULLY!")


if __name__ == "__main__":
    asyncio.run(main())

