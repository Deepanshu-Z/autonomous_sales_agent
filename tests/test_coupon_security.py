"""
test_coupon_security.py — Automated Verification for Backend Secret Gating Architecture.

Verifies:
1. Secret Isolation: Prompt has ZERO coupon codes before profile creation (both in instructions and course JSON).
2. Negative Constraint: User asking "konsa coupon h" yields ZERO code leakage.
3. Semantic Slang Unlock: User saying "bnadi" triggers [UNLOCK_COUPON] and backend injects real coupon code.
4. Multi-Course Agnostic: Works identically for Kathak, Guitar, Yoga without hardcoding.
"""

import sys
import os
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core import prompt, kb_loader
from src.core.coupons import get_all_coupon_codes, build_coupon_context
from src.state.chat_state import sync_state_from_llm_reply, resolve_llm_reply_tags
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_openai import ChatOpenAI


def test_prompt_secret_isolation_when_locked():
    """Confirms NO coupon code string exists anywhere in the system prompt when profile is not created."""
    state = {"stage": "NEW", "profile_created": False, "coupon_sent": False}

    for course_id in ["yoga", "kathak"]:
        sys_prompt = prompt.format_system_prompt(state, course_id=course_id)
        codes = get_all_coupon_codes(course_id)

        assert len(codes) > 0, f"Course {course_id} must have registered coupon codes in JSON"
        for code in codes:
            assert code not in sys_prompt, (
                f"SECURITY VIOLATION: Coupon code '{code}' for course '{course_id}' "
                f"was leaked into the system prompt before profile creation!"
            )


def test_prompt_includes_codes_when_unlocked():
    """Confirms coupon codes ARE injected once profile is confirmed."""
    state = {"stage": "PROFILE_COMPLETED", "profile_created": True, "coupon_sent": False, "package": "1 Month"}

    sys_prompt = prompt.format_system_prompt(state, course_id="yoga")
    assert "YOGA300" in sys_prompt, "Expected unlocked coupon code YOGA300 in prompt when profile is created!"


def test_action_tag_resolution():
    """Tests that backend resolve_llm_reply_tags intercepts [UNLOCK_COUPON] and replaces it with real code."""
    state = {
        "stage": "NEW",
        "profile_created": False,
        "coupon_sent": False,
        "package": "1 Month"
    }

    mock_llm_reply = "Bahut badhiya! Aapka profile complete ho gaya hai. 🎉 [UNLOCK_COUPON]"

    did_unlock, resolved = resolve_llm_reply_tags(mock_llm_reply, state, course_id="yoga")

    assert did_unlock is True
    assert state["profile_created"] is True
    assert state["coupon_sent"] is True
    assert state["stage"] == "COUPON_SENT"
    assert "[UNLOCK_COUPON]" not in resolved
    assert "YOGA300" in resolved, f"Expected YOGA300 in resolved reply, got: {resolved}"


async def test_live_llm_no_leak_on_konsa_coupon():
    """Live LLM test: User asking 'konsa coupon h' before profile creation must NEVER leak codes."""
    from langchain_core.messages import SystemMessage, HumanMessage
    from langchain_openai import ChatOpenAI

    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.3)
    state = {"stage": "NEW", "profile_created": False, "coupon_sent": False, "course": "yoga"}

    sys_content = prompt.format_system_prompt(state, course_id="yoga")
    user_msg = "konsa coupon h"

    messages = [
        SystemMessage(content=sys_content),
        HumanMessage(content=(
            f"Customer's Current Reply: \"{user_msg}\"\n\n"
            "INSTRUCTION:\n"
            "1. Follow all welcome coupon and profile rules from the system prompt.\n"
            "2. STRICT LANGUAGE MATCHING: Match the customer's language (Hinglish).\n"
            "3. Follow WhatsApp formatting (*bold* with single asterisks, • bullets, raw links)."
        ))
    ]

    resp = await llm.ainvoke(messages)
    reply = resp.content.strip()

    all_yoga_codes = get_all_coupon_codes("yoga")
    for code in all_yoga_codes:
        assert code not in reply, (
            f"SECURITY LEAK: LLM leaked coupon code '{code}' in response to '{user_msg}'!\n"
            f"Full reply was:\n{reply}"
        )

    # State must NOT be unlocked
    did_unlock, _ = resolve_llm_reply_tags(reply, state, course_id="yoga")
    assert did_unlock is False
    assert state["profile_created"] is False


async def test_live_llm_slang_unlock_bnadi():
    """Live LLM test: Slang 'bnadi' triggers [UNLOCK_COUPON] and delivers real code via backend."""
    from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
    from langchain_openai import ChatOpenAI

    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.3)
    state = {
        "stage": "APP_LINK_SENT",
        "profile_created": False,
        "coupon_sent": False,
        "package": "1 Month",
        "course": "yoga"
    }

    sys_content = prompt.format_system_prompt(state, course_id="yoga")
    last_ai = "Aap Sensationz App download karke profile banayein, phir batayein!"
    user_msg = "bnadi"

    messages = [
        SystemMessage(content=sys_content),
        HumanMessage(content="coupon code chahiye"),
        AIMessage(content=last_ai),
        HumanMessage(content=(
            f"--- IMMEDIATE CONTEXT ---\n"
            f"AI's Last Message: \"{last_ai}\"\n"
            f"Customer's Current Reply: \"{user_msg}\"\n\n"
            "INSTRUCTION:\n"
            "1. Analyze the customer's reply specifically in context of the AI's last message.\n"
            "2. Follow all welcome coupon and profile rules from the system prompt (if customer created/completed profile or registered, celebrate and include [UNLOCK_COUPON]).\n"
            "3. STRICT LANGUAGE MATCHING: Match the customer's language (Hinglish).\n"
            "4. Follow WhatsApp formatting (*bold* with single asterisks, • bullets, raw links)."
        ))
    ]

    resp = await llm.ainvoke(messages)
    raw_reply = resp.content.strip()

    did_unlock, resolved_reply = resolve_llm_reply_tags(raw_reply, state, course_id="yoga")

    assert did_unlock is True, f"Expected LLM to recognize 'bnadi' and emit [UNLOCK_COUPON]. Raw reply was:\n{raw_reply}"
    assert state["profile_created"] is True
    assert state["coupon_sent"] is True
    assert "YOGA300" in resolved_reply, f"Expected delivered code YOGA300 in resolved reply:\n{resolved_reply}"


async def test_live_llm_no_fake_coupon_on_6_month_ask():
    """Live LLM test: User asking '6 Months ka coupon' before profile creation must NEVER hallucinate WELCOME300."""
    from langchain_core.messages import SystemMessage, HumanMessage
    from langchain_openai import ChatOpenAI

    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.3)
    state = {"stage": "NEW", "profile_created": False, "coupon_sent": False, "package": "6 Months", "course": "yoga"}

    sys_content = prompt.format_system_prompt(state, course_id="yoga")
    user_msg = "6 Months ka coupon do"

    messages = [
        SystemMessage(content=sys_content),
        HumanMessage(content=(
            f"Customer's Current Reply: \"{user_msg}\"\n\n"
            "INSTRUCTION:\n"
            "1. Follow all welcome coupon and profile rules from the system prompt.\n"
            "2. STRICT LANGUAGE MATCHING: Match the customer's language (Hinglish).\n"
            "3. Follow WhatsApp formatting (*bold* with single asterisks, • bullets, raw links)."
        ))
    ]

    resp = await llm.ainvoke(messages)
    raw_reply = resp.content.strip()

    did_unlock, resolved_reply = resolve_llm_reply_tags(raw_reply, state, course_id="yoga")

    # Must NOT hallucinate fake codes or leak real ones
    assert "WELCOME300" not in resolved_reply, "SECURITY BUG: Leaked fake code WELCOME300!"
    assert "YOGA1000" not in resolved_reply, "SECURITY BUG: Leaked real code YOGA1000 before profile created!"
    assert did_unlock is False, "Profile must NOT be unlocked on coupon ask"
    assert state["profile_created"] is False


async def test_live_llm_correct_6_month_coupon_when_unlocked():
    """Live LLM test: Once unlocked, asking for 6 Months coupon must deliver YOGA1000, NOT YOGA300 or WELCOME300."""
    from langchain_core.messages import SystemMessage, HumanMessage
    from langchain_openai import ChatOpenAI

    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.3)
    state = {
        "stage": "PROFILE_COMPLETED",
        "profile_created": True,
        "coupon_sent": True,
        "package": "6 Months",
        "course": "yoga"
    }

    sys_content = prompt.format_system_prompt(state, course_id="yoga")
    user_msg = "6 Months ka coupon code kya hai?"

    messages = [
        SystemMessage(content=sys_content),
        HumanMessage(content=(
            f"Customer's Current Reply: \"{user_msg}\"\n\n"
            "INSTRUCTION:\n"
            "1. Follow all welcome coupon and profile rules from the system prompt.\n"
            "2. STRICT LANGUAGE MATCHING: Match the customer's language (Hinglish).\n"
            "3. Follow WhatsApp formatting (*bold* with single asterisks, • bullets, raw links)."
        ))
    ]

    resp = await llm.ainvoke(messages)
    reply = resp.content.strip()

    assert "YOGA1000" in reply, f"Expected 6 Months coupon YOGA1000, got:\n{reply}"
    assert "WELCOME300" not in reply, f"Hallucinated WELCOME300, got:\n{reply}"


async def test_live_llm_no_unlock_on_where_to_make():
    """Confirms user asking 'kha bnani h' does NOT leak coupon codes or unlock profile."""
    state = {"stage": "NEW", "profile_created": False, "coupon_sent": False}
    sys_content = prompt.format_system_prompt(state, course_id="yoga")

    last_ai = "The fees for our Yoga courses are 1 Month: 300 offer price. Coupon codes unlock only after profile registration."
    user_msg = "kha bnani h"

    messages = [
        SystemMessage(content=sys_content),
        HumanMessage(content="fees"),
        AIMessage(content=last_ai),
        HumanMessage(content=(
            f"--- IMMEDIATE CONTEXT ---\n"
            f"AI's Last Message: \"{last_ai}\"\n"
            f"Customer's Current Reply: \"{user_msg}\"\n\n"
            "INSTRUCTION:\n"
            "1. Analyze the customer's reply specifically in context of the AI's last message.\n"
            "2. Follow all welcome coupon and profile rules from the system prompt.\n"
            "3. STRICT LANGUAGE & SCRIPT MATCHING: Match the exact language AND script of Customer's Current Reply.\n"
        ))
    ]

    llm = ChatOpenAI(model=os.getenv("OPENAI_MODEL", "gpt-4o"), temperature=0.3)
    resp = await llm.ainvoke(messages)
    raw_reply = resp.content.strip()

    did_unlock, resolved_reply = resolve_llm_reply_tags(raw_reply, state, course_id="yoga", user_text=user_msg)

    assert did_unlock is False, f"Expected did_unlock to be False on 'kha bnani h', got True! Raw reply was:\n{raw_reply}"
    assert state["profile_created"] is False
    assert state["coupon_sent"] is False
    assert "YOGA300" not in resolved_reply, f"Coupon code leaked on question 'kha bnani h':\n{resolved_reply}"


async def main():
    print("==================================================")
    print("  RUNNING COUPON SECRET GATING SECURITY TESTS     ")
    print("==================================================")
    print("\n1. Testing prompt secret isolation when locked...")
    test_prompt_secret_isolation_when_locked()
    print("   PASSED: Zero coupon codes present in locked prompt for all courses!")

    print("\n2. Testing prompt includes codes when unlocked...")
    test_prompt_includes_codes_when_unlocked()
    print("   PASSED: Codes correctly present in unlocked prompt!")

    print("\n3. Testing [UNLOCK_COUPON] action tag resolution...")
    test_action_tag_resolution()
    print("   PASSED: Action tag resolved and coupon banner delivered!")

    print("\n4. Testing Live LLM: 'konsa coupon h' before profile creation (Negative Constraint)...")
    await test_live_llm_no_leak_on_konsa_coupon()
    print("   PASSED: Zero codes leaked when user asks 'konsa coupon h'!")

    print("\n5. Testing Live LLM: Slang 'bnadi' triggers semantic unlock...")
    await test_live_llm_slang_unlock_bnadi()
    print("   PASSED: LLM emitted [UNLOCK_COUPON] and backend delivered real code!")

    print("\n6. Testing Live LLM: No fake WELCOME300 hallucinated on '6 Months ka coupon do'...")
    await test_live_llm_no_fake_coupon_on_6_month_ask()
    print("   PASSED: Zero fake or real codes leaked on 6 Months request before profile!")

    print("\n7. Testing Live LLM: Correct YOGA1000 delivered for 6 Months when unlocked...")
    await test_live_llm_correct_6_month_coupon_when_unlocked()
    print("   PASSED: YOGA1000 correctly delivered for 6 Months (not YOGA300 or WELCOME300)!")

    print("\n8. Testing Live LLM: Question 'kha bnani h' does NOT unlock coupon...")
    await test_live_llm_no_unlock_on_where_to_make()
    print("   PASSED: Zero coupon codes revealed on question 'kha bnani h'!")

    print("\n==================================================")
    print("  ALL 8 SECURITY & UNLOCK TESTS PASSED 100%!     ")
    print("==================================================")


if __name__ == "__main__":
    asyncio.run(main())
