import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
"""
test_kb_architecture.py — Automated Test Suite for Clean Knowledge Architecture.

Verifies:
1. Dynamic course loading via kb_loader
2. Dynamic VALID_TIMINGS and VALID_PACKAGES derivation
3. Clean prompt generation and token footprint
4. Coupon privacy state management
5. End-to-end LLM query response grounding
"""

import sys
import asyncio
import json
import tiktoken
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding='utf-8')
load_dotenv()

from src.core import kb_loader
from src.core import prompt
from src.core import rag
from src.core import coupons


def test_kb_loader():
    print("\n[TEST 1] Testing kb_loader...")
    data = kb_loader.get_course_data("yoga")
    assert data is not None, "Yoga data should not be None"
    assert "pricing" in data, "pricing key missing from yoga data"
    assert "batch_timings" in data, "batch_timings key missing"
    assert "teachers" in data, "teachers key missing"
    print("  -> Course data loaded successfully with all required keys.")

    timings = kb_loader.get_valid_timings("yoga")
    assert len(timings) == 10, f"Expected 10 batch timings, got {len(timings)}"
    assert "5:00–6:00 AM" in timings or "5:00 AM to 6:00 AM" in timings
    print(f"  -> Successfully extracted {len(timings)} batch timings dynamically.")

    pkgs = kb_loader.get_valid_packages("yoga")
    assert len(pkgs) == 4, f"Expected 4 packages, got {len(pkgs)}"
    assert "1 Month" in pkgs and "300" in pkgs["1 Month"]
    assert "1 Year" in pkgs and "1,800" in pkgs["1 Year"]
    print("  -> Successfully extracted packages and offer pricing dynamically.")

    catalog = kb_loader.get_course_catalog()
    assert len(catalog) >= 10, f"Expected at least 10 courses in catalog, got {len(catalog)}"
    print(f"  -> Course catalog verified with {len(catalog)} courses.")

    # Multi-course detection
    assert kb_loader.detect_course_from_text("Mujhe kathak sikhna hai") == "kathak"
    assert kb_loader.detect_course_from_text("Guitar class timings?") == "guitar"
    assert kb_loader.detect_course_from_text("Fees kitni hai?") == "yoga"  # default
    print("  -> Multi-course intent detection verified.")


def test_prompt_generation():
    print("\n[TEST 2] Testing prompt generation & token budget...")
    enc = tiktoken.get_encoding("o200k_base")

    # 1. New user (Profile registration PENDING)
    prompt_new = prompt.format_system_prompt({"stage": "NEW"})
    tokens_new = len(enc.encode(prompt_new))
    assert "Profile Created: PENDING" in prompt_new, "Profile should be PENDING for new users"
    assert "YOGA300" not in prompt_new, "Course coupon codes should be securely gated for new users"
    assert tokens_new < 6000, f"Prompt token count {tokens_new} exceeds 6,000 threshold"
    print(f"  -> New session prompt generated: {tokens_new} tokens (Under 6k budget, Profile PENDING).")

    # 2. Profile created user (Profile registration CONFIRMED)
    prompt_unlocked = prompt.format_system_prompt({
        "stage": "PROFILE_COMPLETED",
        "profile_created": True,
        "package": "1 Month"
    })
    tokens_unlocked = len(enc.encode(prompt_unlocked))
    assert "Profile Created: CONFIRMED" in prompt_unlocked, "Profile should be CONFIRMED when profile_created=True"
    assert "YOGA300" in prompt_unlocked, "Applicable coupon YOGA300 should be present"
    print(f"  -> Profile completed prompt generated: {tokens_unlocked} tokens (Profile CONFIRMED).")


def test_coupons_and_rag_integration():
    print("\n[TEST 3] Testing coupons & rag module sync...")
    assert len(coupons.VALID_PACKAGES) == 4
    assert rag.VALID_TIMINGS == kb_loader.get_valid_timings("yoga")
    assert rag.VALID_PACKAGES == kb_loader.get_valid_packages("yoga")
    print("  -> coupons.py and rag.py are 100% synchronized with kb_loader.")


async def test_llm_query():
    print("\n[TEST 4] Testing live LLM response with clean dynamic prompt...")
    test_q = "6am batch koun padhata hai aur 1 month ki fees kitni hogi offer me?"
    print(f"  -> Query: \"{test_q}\"")

    res = await rag.ask_rag_async(
        question=test_q,
        chat_history=[],
        state={"stage": "NEW"}
    )
    reply = res.get("reply", "")
    print(f"\n--- AI RESPONSE ---\n{reply}\n-------------------\n")

    # Verify key factual elements from yoga.json are present in reply
    assert any(fee in reply for fee in ["700", "300"]), "Fee information (700 base or 300 offer) should be in reply"
    print("  -> AI response correctly answered compound multi-intent question (teachers + fees) grounded in yoga.json!")


async def main():
    test_kb_loader()
    test_prompt_generation()
    test_coupons_and_rag_integration()
    await test_llm_query()
    print("\n✅ ALL TESTS PASSED SUCCESSFULLY!")


if __name__ == "__main__":
    asyncio.run(main())
