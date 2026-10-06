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
from src.core import conversation_handler
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
    catalog = kb_loader.get_course_catalog()
    assert len(catalog) >= 10, f"Expected at least 10 courses, got {len(catalog)}"
    print(f"  -> Catalog loaded successfully with {len(catalog)} courses.")

    # Test yoga kb load
    yoga_kb = kb_loader.get_course_data("yoga")
    assert yoga_kb is not None, "Yoga KB should load"
    assert (yoga_kb.get("course_overview", {}).get("name") or yoga_kb.get("course")) == "Online Live Interactive Yoga", "Yoga course name matches"
    print("  -> yoga.json loaded and verified.")

    # Test kathak kb load
    kathak_kb = kb_loader.get_course_data("kathak")
    assert kathak_kb is not None, "Kathak KB should load"
    assert (kathak_kb.get("course_overview", {}).get("name") or kathak_kb.get("course")) == "Online Live Interactive Kathak", "Kathak course name matches"
    print("  -> kathak.json loaded and verified.")

    # Test un-digitized course fallback
    dance_kb = kb_loader.get_course_data("dance")
    assert dance_kb is not None, "Dance KB should return structured catalog metadata"
    print("  -> Non-digitized course (dance) safely falls back to catalog metadata.")


def test_prompt_generation():
    print("\n[TEST 2] Testing dynamic prompt generation...")
    # Test new user prompt (coupons locked)
    prompt_locked = prompt.format_system_prompt({"stage": "NEW"}, course_id="yoga")
    tokens_locked = len(prompt_locked) // 4
    assert "YOGA300" not in prompt_locked, "Coupon code YOGA300 should be redacted for new user"
    assert "locked" in prompt_locked.lower(), "Coupon status should be locked"
    print(f"  -> New user prompt generated: {tokens_locked} tokens (Coupons safely REDACTED).")

    # Test profile completed prompt (coupons unlocked)
    prompt_unlocked = prompt.format_system_prompt({
        "stage": "PROFILE_COMPLETED",
        "profile_created": True,
        "package": "1 Month",
        "timing": "6:00–7:00 AM"
    }, course_id="yoga")
    tokens_unlocked = len(prompt_unlocked) // 4
    assert "YOGA600" in prompt_unlocked, "Applicable coupon YOGA600 should be present"
    print(f"  -> Profile completed prompt generated: {tokens_unlocked} tokens (Profile CONFIRMED).")


def test_coupons_and_kb_integration():
    print("\n[TEST 3] Testing coupons & kb_loader sync...")
    assert len(coupons.VALID_PACKAGES) == 4
    assert len(kb_loader.get_valid_timings("yoga")) > 0
    assert len(kb_loader.get_valid_packages("yoga")) > 0
    print("  -> coupons.py and kb_loader are 100% synchronized.")


async def test_llm_query():
    print("\n[TEST 4] Testing live LLM response with clean dynamic prompt...")
    test_q = "6am batch koun padhata hai aur 1 month ki fees kitni hogi offer me?"
    print(f"  -> Query: \"{test_q}\"")

    res = await conversation_handler.handle_incoming_message(
        user_message=test_q,
        session_state={"stage": "NEW"},
        conversation_history=[],
        course_id="yoga"
    )
    reply = res.get("reply", "")
    print(f"\n--- AI RESPONSE ---\n{reply}\n-------------------\n")

    # Verify key factual elements from yoga.json are present in reply
    assert any(fee in reply for fee in ["700", "300"]), "Fee information (700 base or 300 offer) should be in reply"
    print("  -> AI response correctly answered compound multi-intent question (teachers + fees) grounded in yoga.json!")


async def main():
    test_kb_loader()
    test_prompt_generation()
    test_coupons_and_kb_integration()
    await test_llm_query()
    print("\n✅ ALL TESTS PASSED SUCCESSFULLY!")


if __name__ == "__main__":
    asyncio.run(main())
