"""
test_refusal_and_followup.py — Automated Test Suite for Contextual Follow-up & Refusal Engines.

Tests:
1. Contextual follow-up generation with curiosity & engagement (zero hardcoded strings)
2. Follow-up suppression rules
3. Refusal without reason -> prompts for reason with empathy
4. Refusal with resolvable reason -> offers knowledge-backed solution
5. Refusal with unserviceable reason (no solution) -> escalates to agent immediately
6. Refusal with legal / dispute / safety risk -> escalates to agent immediately
7. Round-robin agent assignment across the 2 configured agents
"""

import sys
import os
import asyncio
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")
load_dotenv()

from src.core.contextual_followup import (
    generate_contextual_followup_async,
    should_suppress_followup,
)
from src.core.refusal_engine import evaluate_refusal_async
from src.services.agent_handoff import get_next_agent_email, AGENT_POOL


# ---------------------------------------------------------------------------
# Test 1: Agent Pool Configuration (User constraint: 2 agents from .env)
# ---------------------------------------------------------------------------
def test_agent_pool_configuration():
    print("\n--- Test 1: Agent Pool Configuration ---")
    assert len(AGENT_POOL) == 2, f"Expected exactly 2 agents in pool, got {len(AGENT_POOL)}: {AGENT_POOL}"
    agent1, idx1 = get_next_agent_email()
    agent2, idx2 = get_next_agent_email()
    agent3, idx3 = get_next_agent_email()
    print(f"Agent turn 1: {agent1} (idx={idx1})")
    print(f"Agent turn 2: {agent2} (idx={idx2})")
    print(f"Agent turn 3: {agent3} (idx={idx3})")
    assert agent1 != agent2, "Round robin should alternate between the 2 agents"
    assert agent1 == agent3, "Agent 3 should loop back to agent 1"
    print("  -> Round robin between 2 agents passed!")


# ---------------------------------------------------------------------------
# Test 2: Contextual Engagement Follow-Up Engine (Curiosity & Questions)
# ---------------------------------------------------------------------------
async def test_contextual_followup_generation():
    print("\n--- Test 2: Dynamic Contextual Follow-Up Generation ---")
    
    # Scenario A: User asks about batch timings in English
    user_q = "What are the morning class timings for yoga?"
    ai_ans = "Our morning yoga batches are held live from 5:00 AM to 11:00 AM (e.g., 6:00–7:00 AM, 7:00–8:00 AM, 8:00–9:00 AM) Monday to Friday."
    state = {"stage": "TIMING_ASKED", "course": "yoga"}
    
    followup = await generate_contextual_followup_async(user_q, ai_ans, state=state)
    print(f"\nUser query : {user_q}")
    print(f"AI answer  : {ai_ans}")
    print(f"Follow-up  : {followup}")
    assert followup is not None, "Expected contextual follow-up message to be generated"
    assert len(followup) > 10, "Follow-up should be substantive"
    assert "?" in followup, "Follow-up should contain an engaging question"
    
    # Scenario B: User asks in Hinglish about beginner fitness
    user_q2 = "Main kabhi yoga nahi kiya, kya mere liye ye class mushkil hogi?"
    ai_ans2 = "Bilkul nahi! Hamare batches beginners ke liye perfect hain. Certified trainers step-by-step simple asanas se start karwate hain."
    followup2 = await generate_contextual_followup_async(user_q2, ai_ans2, state=state)
    print(f"\nUser query : {user_q2}")
    print(f"AI answer  : {ai_ans2}")
    print(f"Follow-up  : {followup2}")
    assert followup2 is not None
    assert "?" in followup2, "Follow-up should contain an engaging question"
    print("  -> Dynamic contextual follow-up generation passed!")


# ---------------------------------------------------------------------------
# Test 3: Follow-Up Suppression Rules
# ---------------------------------------------------------------------------
def test_followup_suppression():
    print("\n--- Test 3: Follow-Up Suppression Rules ---")
    assert should_suppress_followup("hi", "hello", {"is_escalated": True}) is True
    assert should_suppress_followup("thanks", "welcome", {"stage": "COUPON_SENT"}) is True
    assert should_suppress_followup("thanks", "welcome", {"stage": "PROFILE_COMPLETED"}) is True
    assert should_suppress_followup("connect to agent please", "Connecting...", {}) is True
    print("  -> Suppression rules verified!")


# ---------------------------------------------------------------------------
# Test 4: Refusal Without Reason -> Asks Reason
# ---------------------------------------------------------------------------
async def test_refusal_without_reason():
    print("\n--- Test 4: Refusal Without Reason ---")
    declines = [
        "I am not interested.",
        "Nahi chahiye abhi, thanks.",
        "No, I don't want to join.",
    ]
    for text in declines:
        eval_res = await evaluate_refusal_async(text, state={"stage": "TIMING_SELECTED", "course": "yoga"})
        print(f"\nInput: '{text}'")
        print(f"Decision: is_refusal={eval_res.get('is_refusal')}, resolution={eval_res.get('resolution_type')}")
        print(f"Bot reply: {eval_res.get('reply_message')}")
        assert eval_res.get("is_refusal") is True, f"Failed to detect refusal in: {text}"
        assert eval_res.get("has_reason") is False, f"Should not have reason for: {text}"
        assert eval_res.get("resolution_type") == "ASK_REASON"
        assert eval_res.get("reply_message") is not None
    print("  -> Refusal without reason correctly asks for reason!")


# ---------------------------------------------------------------------------
# Test 5: Refusal With Resolvable Reason (Price / Timing) -> Provides KB Solution
# ---------------------------------------------------------------------------
async def test_refusal_with_resolvable_reason():
    print("\n--- Test 5: Refusal With Resolvable Reason ---")
    # Budget concern
    text1 = "Nahi lena, fees bohot zyada lag rahi hai mere budget ke bahar hai."
    eval1 = await evaluate_refusal_async(text1, state={"course": "yoga"})
    print(f"\nInput: '{text1}'")
    print(f"Decision: resolution={eval1.get('resolution_type')}, solution_viable={eval1.get('solution_viable')}")
    print(f"Bot reply: {eval1.get('reply_message')}")
    assert eval1.get("is_refusal") is True
    assert eval1.get("has_reason") is True
    assert eval1.get("solution_viable") is True
    assert eval1.get("resolution_type") == "PROVIDE_SOLUTION"

    # Busy schedule concern
    text2 = "I don't have time in the morning due to my shift work."
    eval2 = await evaluate_refusal_async(text2, state={"course": "yoga"})
    print(f"\nInput: '{text2}'")
    print(f"Decision: resolution={eval2.get('resolution_type')}, solution_viable={eval2.get('solution_viable')}")
    print(f"Bot reply: {eval2.get('reply_message')}")
    assert eval2.get("is_refusal") is True
    assert eval2.get("has_reason") is True
    assert eval2.get("solution_viable") is True
    assert eval2.get("resolution_type") == "PROVIDE_SOLUTION"
    print("  -> Resolvable refusals correctly offer knowledge-backed solutions!")


# ---------------------------------------------------------------------------
# Test 6: Refusal With Unserviceable Reason (No Solution in KB) -> Escalates
# ---------------------------------------------------------------------------
async def test_refusal_with_no_solution():
    print("\n--- Test 6: Refusal With Unserviceable Reason (No Solution in KB) ---")
    text = "I will only join if you send an offline trainer to my home in Bangalore. Online classes are useless to me."
    eval_res = await evaluate_refusal_async(text, state={"course": "yoga"})
    print(f"\nInput: '{text}'")
    print(f"Decision: resolution={eval_res.get('resolution_type')}, solution_viable={eval_res.get('solution_viable')}")
    print(f"Bot reply: {eval_res.get('reply_message')}")
    print(f"Escalation Reason: {eval_res.get('escalation_reason')}")
    assert eval_res.get("is_refusal") is True
    assert eval_res.get("resolution_type") == "ESCALATE_TO_AGENT"
    assert eval_res.get("reply_message") is not None
    print("  -> Unserviceable refusal correctly triggers agent escalation!")


# ---------------------------------------------------------------------------
# Test 7: Refusal With Legal / Dispute / Risk -> Escalates Immediately
# ---------------------------------------------------------------------------
async def test_refusal_with_legal_or_risk():
    print("\n--- Test 7: Refusal With Legal / Dispute / Risk ---")
    risk_texts = [
        "Your website deducted money from my account without authorization! I will file a police complaint and drag you to consumer court!",
        "Fraud company! I am taking legal action against you immediately.",
    ]
    for text in risk_texts:
        eval_res = await evaluate_refusal_async(text, state={"course": "yoga"})
        print(f"\nInput: '{text}'")
        print(f"Risk: has_risk={eval_res.get('has_risk_or_legal')}, category={eval_res.get('risk_category')}")
        print(f"Decision: resolution={eval_res.get('resolution_type')}")
        print(f"Bot reply: {eval_res.get('reply_message')}")
        print(f"Escalation Reason: {eval_res.get('escalation_reason')}")
        assert eval_res.get("has_risk_or_legal") is True, f"Failed to detect risk in: {text}"
        assert eval_res.get("resolution_type") == "ESCALATE_TO_AGENT", f"Failed to escalate: {text}"
        assert eval_res.get("reply_message") is not None
        assert eval_res.get("escalation_reason") is not None
    print("  -> Legal and risk situations immediately escalate with query acknowledgement!")


# ---------------------------------------------------------------------------
# Test 8: Non-Refusal Informational Queries -> NOT_A_REFUSAL
# ---------------------------------------------------------------------------
async def test_non_refusal_queries():
    print("\n--- Test 8: Non-Refusal Informational Queries ---")
    info_queries = [
        "What is the fee for 3 months?",
        "Can I pay via UPI?",
        "Do you provide a certificate after the course?",
    ]
    for q in info_queries:
        eval_res = await evaluate_refusal_async(q, state={"course": "yoga"})
        assert eval_res.get("resolution_type") == "NOT_A_REFUSAL"
    print("  -> Genuine questions are not falsely flagged as refusals!")


async def main():
    test_agent_pool_configuration()
    test_followup_suppression()
    await test_contextual_followup_generation()
    await test_refusal_without_reason()
    await test_refusal_with_resolvable_reason()
    await test_refusal_with_no_solution()
    await test_refusal_with_legal_or_risk()
    await test_non_refusal_queries()
    print("\n🎉 ALL REFUSAL & FOLLOW-UP TESTS PASSED SUCCESSFULLY! 🎉\n")


if __name__ == "__main__":
    asyncio.run(main())
