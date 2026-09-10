import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sys
import os
import asyncio
import time
import uuid

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# Add workspace directory to python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from chat_state import (
    extract_and_update_slots,
    get_user_state,
    save_user_state,
    matches_any,
    advance_stage,
)
from tasks import (
    process_incoming_message_async,
    INFO_INTENT_KEYWORDS,
    AGENT_TRIGGER_WORDS,
)
from batching import add_message_to_batch_async
from conversation_handler import handle_incoming_message

async def ask_rag_async(query: str, state: dict = None):
    return await handle_incoming_message(
        user_message=query, session_state=state or {"stage": "NEW"}, conversation_history=[], course_id="yoga"
    )

results = {}

def log_test_result(name: str, passed: bool, msg: str = ""):
    results[name] = (passed, msg)
    status = "PASS" if passed else "FAIL"
    safe_msg = str(msg).encode('ascii', errors='replace').decode('ascii')
    print(f"[{status}] {name}: {safe_msg}")

# Issue 13 test
async def test_issue_13_yoga_keyword_reachable():
    try:
        passed_yoga = "yoga" in INFO_INTENT_KEYWORDS
        passed_eligible = "eligible" in INFO_INTENT_KEYWORDS
        passed_freq = "classes per week" in INFO_INTENT_KEYWORDS
        if passed_yoga and passed_eligible and passed_freq:
            log_test_result("Issue 13 — Yoga keywords reachable", True, "All keywords exist in INFO_INTENT_KEYWORDS list.")
        else:
            log_test_result("Issue 13 — Yoga keywords reachable", False, f"Missing: yoga={passed_yoga}, eligible={passed_eligible}, classes_per_week={passed_freq}")
    except Exception as e:
        log_test_result("Issue 13 — Yoga keywords reachable", False, str(e))

# Issue 9 test
async def test_issue_9_hai_false_positive():
    try:
        from chat_state import CONFIRMATION_WORDS
        # "Kya result hai ya nahi" -> contains "hai" which has "ha" substring, but we use matches_any
        txt = "Kya result hai ya nahi"
        res = matches_any(txt, CONFIRMATION_WORDS)
        if not res:
            log_test_result("Issue 9 — Hindi 'hai' false positive", True, "Successfully avoided matching 'hai' as confirmation.")
        else:
            log_test_result("Issue 9 — Hindi 'hai' false positive", False, "Incorrectly classified 'hai' as confirmation.")
    except Exception as e:
        log_test_result("Issue 9 — Hindi 'hai' false positive", False, str(e))

# Issue 11 test
async def test_issue_11_stage_does_not_regress():
    try:
        s1 = advance_stage("TIMING_SELECTED", "ENROLL_CONFIRMED")
        s2 = advance_stage("TIMING_SELECTED", "PACKAGE_ASKED")
        if s1 == "TIMING_SELECTED" and s2 == "PACKAGE_ASKED":
            log_test_result("Issue 11 — Stage monotonicity", True, "advance_stage helper prevents regression.")
        else:
            log_test_result("Issue 11 — Stage monotonicity", False, f"Regression check failed: propose ENROLL_CONFIRMED got {s1}, propose PACKAGE_ASKED got {s2}")
    except Exception as e:
        log_test_result("Issue 11 — Stage monotonicity", False, str(e))

# Issue 12 test
async def test_issue_12_supabase_write_succeeds_with_extra_fields():
    try:
        phone = f"99999{str(uuid.uuid4().int)[:5]}"
        state = get_user_state(phone)
        state["stage"] = "NEW"
        state["already_assigned"] = True
        state["arbitrary_unknown_field"] = "some value"
        save_user_state(phone, state)
        log_test_result("Issue 12 — Supabase schema filter", True, "Successfully upserted state with extra runtime fields without PGRST204 mismatch error.")
    except Exception as e:
        log_test_result("Issue 12 — Supabase schema filter", False, str(e))

# Issue 14 test
async def test_issue_14_agent_trigger_words():
    try:
        txt = "I want to talk to a real person"
        res = matches_any(txt, AGENT_TRIGGER_WORDS)
        if res:
            log_test_result("Issue 14 — Agent trigger words", True, "Successfully matched 'real person' as agent trigger.")
        else:
            log_test_result("Issue 14 — Agent trigger words", False, f"Failed to match '{txt}' against {AGENT_TRIGGER_WORDS}")
    except Exception as e:
        log_test_result("Issue 14 — Agent trigger words", False, str(e))

# Issue 1 test
async def test_issue_1_no_duplicate_followup():
    try:
        from tasks import should_skip_followup
        rag_reply = "Our classes timings are morning 7 AM and evening 5 PM. Which timing suits you?"
        res = should_skip_followup("What is the timing?", rag_reply, "ENROLL_CONFIRMED")
        if res:
            log_test_result("Issue 1 — No duplicate follow-up timing", True, "Correctly detected duplicate timing question in LLM response.")
        else:
            log_test_result("Issue 1 — No duplicate follow-up timing", False, "Failed to detect duplicate timing question in LLM response.")
    except Exception as e:
        log_test_result("Issue 1 — No duplicate follow-up timing", False, str(e))

# Issue 4 test
async def test_issue_4_fraud_complaint_not_hijacked():
    try:
        phone = f"99999{str(uuid.uuid4().int)[:5]}"
        state = get_user_state(phone)
        state["stage"] = "TIMING_SELECTED"
        state["timing"] = "5:00–6:00 PM"
        state["package"] = None
        state["is_target_ad"] = True
        save_user_state(phone, state)
        
        import tasks
        original_send = tasks.send_text_message_async
        sent_messages = []
        async def mock_send(phone, text):
            sent_messages.append(text)
        tasks.send_text_message_async = mock_send
        
        await process_incoming_message_async(phone, "Tum fraud ho")
        
        tasks.send_text_message_async = original_send
        
        reply = sent_messages[0] if sent_messages else ""
        if reply and "₹1,750" not in reply and "₹700" not in reply:
            log_test_result("Issue 4 — Fraud complaint not hijacked", True, "Successfully fell through to RAG rather than fast-pathing to package list.")
        else:
            log_test_result("Issue 4 — Fraud complaint not hijacked", False, f"Fast-path guard hijacked or failed: '{reply}'")
    except Exception as e:
        log_test_result("Issue 4 — Fraud complaint not hijacked", False, str(e))

# Issue 5 test
async def test_issue_5_instructor_per_batch_accuracy():
    try:
        state = {"stage": "NEW"}
        res_raw = await ask_rag_async("Who is the instructor for 7:00–8:00 AM morning batch?", state=state)
        res = res_raw["reply"] if isinstance(res_raw, dict) else res_raw
        res_lower = res.lower()
        if "mradula" in res_lower:
            log_test_result("Issue 5 — Instructor accuracy", True, f"Instructor query responded with correct context: '{res}'")
        else:
            log_test_result("Issue 5 — Instructor accuracy", False, f"Potential hallucination or wrong instructor returned: '{res}'")
    except Exception as e:
        log_test_result("Issue 5 — Instructor accuracy", False, str(e))

# Issue 2 & 10 tests
async def test_issue_10_fragmented_question_across_3_messages():
    try:
        phone = f"99999{str(uuid.uuid4().int)[:5]}"
        state = get_user_state(phone)
        state["is_target_ad"] = True
        save_user_state(phone, state)
        import tasks
        original_send = tasks.send_text_message_async
        sent_replies = []
        async def mock_send(p, text):
            sent_replies.append(text)
        tasks.send_text_message_async = mock_send
        
        await add_message_to_batch_async(phone, "Who teaches yoga classes?")
        await asyncio.sleep(0.5)
        await add_message_to_batch_async(phone, "What is the fee?")
        await asyncio.sleep(0.5)
        await add_message_to_batch_async(phone, "Do you have morning batches?")
        
        print("Waiting for debounce window and processing (polling up to 15s)...")
        for _ in range(30):
            if len(sent_replies) > 0:
                break
            await asyncio.sleep(0.5)
        
        tasks.send_text_message_async = original_send
        
        if len(sent_replies) >= 1:
            reply = " ".join(sent_replies).lower()
            has_fee = "fee" in reply or "₹" in reply or "700" in reply
            has_morning = "morning" in reply or "am" in reply or "6:00" in reply
            if has_fee and has_morning:
                log_test_result("Issue 10 — Fragmented / multi-question burst", True, "Successfully debounced and answered all fragmented questions in one response.")
            else:
                log_test_result("Issue 10 — Fragmented / multi-question burst", False, f"Response did not answer all questions: '{sent_replies[0]}'")
        else:
            log_test_result("Issue 10 — Fragmented / multi-question burst", False, f"Expected at least 1 response, got {len(sent_replies)}")

    except Exception as e:
        log_test_result("Issue 10 — Fragmented / multi-question burst", False, str(e))

async def test_issue_2_multiquestion_batch():
    try:
        state = {"stage": "NEW"}
        query = "Who is the teacher for the morning batches? What are the fees? Do you offer demo classes?"
        res_raw = await ask_rag_async(query, state=state)
        res = res_raw["reply"] if isinstance(res_raw, dict) else res_raw
        res_lower = res.lower()
        has_teacher = "priya" in res_lower or "suman" in res_lower or "instructor" in res_lower
        has_fee = "fee" in res_lower or "₹" in res_lower or "700" in res_lower
        has_demo = "demo" in res_lower or "youtube" in res_lower or "link" in res_lower
        if has_teacher and has_fee and has_demo:
            log_test_result("Issue 2 — Multi-question batch", True, "RAG answered all distinct questions.")
        else:
            log_test_result("Issue 2 — Multi-question batch", False, f"Response missing answers: teacher={has_teacher}, fee={has_fee}, demo={has_demo}. Reply: '{res}'")
    except Exception as e:
        log_test_result("Issue 2 — Multi-question batch", False, str(e))

async def main():
    print("==================================================")
    print("Running Regression Test Suite...")
    print("==================================================")
    
    await test_issue_13_yoga_keyword_reachable()
    await test_issue_9_hai_false_positive()
    await test_issue_11_stage_does_not_regress()
    await test_issue_12_supabase_write_succeeds_with_extra_fields()
    await test_issue_14_agent_trigger_words()
    await test_issue_1_no_duplicate_followup()
    await test_issue_4_fraud_complaint_not_hijacked()
    await test_issue_5_instructor_per_batch_accuracy()
    await test_issue_10_fragmented_question_across_3_messages()
    await test_issue_2_multiquestion_batch()
    
    print("\n==================================================")
    print("TEST SUITE SUMMARY")
    print("==================================================")
    all_passed = True
    for name, (passed, msg) in results.items():
        status = "PASS" if passed else "FAIL"
        if not passed:
            all_passed = False
        safe_msg = str(msg).encode('ascii', errors='replace').decode('ascii')
        print(f"[{status}] - {name}: {safe_msg}")
    print("==================================================")
    
    if all_passed:
        print("ALL TESTS PASSED SUCCESSFULLY!")
        sys.exit(0)
    else:
        print("SOME TESTS FAILED!")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
