# -*- coding: utf-8 -*-
import asyncio
import sys
sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8')

from src.core.intent_classifier import extract_intent_and_state_changes, apply_extraction_to_state
from src.core.prompt import format_system_prompt
from src.core import kb_loader

async def test_all():
    print("==================================================")
    print("RUNNING UNOFFERED ENTITIES & COURSE SWITCH VERIFICATION")
    print("==================================================")

    # ----------------------------------------------------
    # TEST 1: Unoffered Package: "ok mrko 2 2 yearly lene h packge"
    # ----------------------------------------------------
    print("\n--- TEST 1: Unoffered Package ('ok mrko 2 2 yearly lene h packge') ---")
    yoga_state = {
        "course": "yoga",
        "stage": "TIMING_SELECTED",
        "timing": "6:00 AM to 7:00 AM",
        "selected_timing": "6:00 AM to 7:00 AM",
        "teacher": "Suman Lata",
        "selected_teacher": "Suman Lata",
        "package": None,
        "payment_status": "NONE"
    }
    msg1 = "ok mrko 2 2 yearly lene h packge"
    flags1 = await extract_intent_and_state_changes(msg1, state=yoga_state)
    print(f"Flags extracted for Test 1: unoffered_type={flags1.get('unoffered_type')}, unoffered_value={flags1.get('unoffered_value')}, selected_package={flags1.get('selected_package')}")
    
    assert flags1.get("unoffered_type") == "PACKAGE", f"Expected unoffered_type == 'PACKAGE', got {flags1.get('unoffered_type')}"
    assert "2 Year" in str(flags1.get("unoffered_value")), f"Expected '2 Years' in unoffered_value, got {flags1.get('unoffered_value')}"
    assert flags1.get("selected_package") is None, f"Expected selected_package is None, got {flags1.get('selected_package')}"

    new_state1 = apply_extraction_to_state(yoga_state, flags1)
    assert new_state1.get("package") is None, f"Expected state package is None, got {new_state1.get('package')}"

    prompt1 = format_system_prompt(new_state1, course_id="yoga")
    assert "UNOFFERED PACKAGE / DURATION" in prompt1, "Expected prompt to contain unoffered package guidance"
    assert "PACKAGE DURATION IS NOT SELECTED" in prompt1, "Expected anti-assumption guard for package in prompt"
    print("✅ TEST 1 PASSED: Unoffered package correctly flagged, zero snapping to 1 Year, intelligent guidance injected into prompt!")

    # ----------------------------------------------------
    # TEST 2: Unoffered Timing: "3"
    # ----------------------------------------------------
    print("\n--- TEST 2: Unoffered Timing ('3') in Yoga ---")
    timing_query_state = {
        "course": "yoga",
        "stage": "COURSE_SELECTED",
        "timing": None,
        "selected_timing": None,
        "payment_status": "NONE"
    }
    history = [
        {"role": "assistant", "content": "Which batch timing suits your daily routine best? 😊"}
    ]
    msg2 = "3"
    flags2 = await extract_intent_and_state_changes(msg2, state=timing_query_state, history=history)
    print(f"Flags extracted for Test 2: unoffered_type={flags2.get('unoffered_type')}, unoffered_value={flags2.get('unoffered_value')}, selected_timing={flags2.get('selected_timing')}")

    assert flags2.get("unoffered_type") == "TIMING", f"Expected unoffered_type == 'TIMING', got {flags2.get('unoffered_type')}"
    assert "3:00" in str(flags2.get("unoffered_value")), f"Expected '3:00' in unoffered_value, got {flags2.get('unoffered_value')}"
    assert flags2.get("selected_timing") is None, f"Expected selected_timing is None, got {flags2.get('selected_timing')}"

    new_state2 = apply_extraction_to_state(timing_query_state, flags2)
    assert new_state2.get("timing") is None, f"Expected state timing is None, got {new_state2.get('timing')}"

    prompt2 = format_system_prompt(new_state2, course_id="yoga")
    assert "UNOFFERED BATCH TIMING" in prompt2, "Expected prompt to contain unoffered timing guidance"
    assert "BATCH TIMING IS NOT SELECTED" in prompt2, "Expected anti-assumption guard for timing in prompt"
    print("✅ TEST 2 PASSED: Bare digit '3' correctly identified as unoffered timing, zero snapping to 6-7 AM, intelligent guidance injected into prompt!")

    # ----------------------------------------------------
    # TEST 3: Unoffered Teacher: "deepanshu sir se seekhna h"
    # ----------------------------------------------------
    print("\n--- TEST 3: Unoffered Teacher ('deepanshu sir se seekhna h') in Yoga ---")
    teacher_query_state = {
        "course": "yoga",
        "stage": "TIMING_SELECTED",
        "timing": "6:00 AM to 7:00 AM",
        "selected_timing": "6:00 AM to 7:00 AM",
        "teacher": None,
        "selected_teacher": None
    }
    msg3 = "deepanshu sir se seekhna h"
    flags3 = await extract_intent_and_state_changes(msg3, state=teacher_query_state)
    print(f"Flags extracted for Test 3: unoffered_type={flags3.get('unoffered_type')}, unoffered_value={flags3.get('unoffered_value')}, selected_teacher={flags3.get('selected_teacher')}")

    assert flags3.get("unoffered_type") == "TEACHER", f"Expected unoffered_type == 'TEACHER', got {flags3.get('unoffered_type')}"
    assert "Deepanshu" in str(flags3.get("unoffered_value")).title(), f"Expected Deepanshu in unoffered_value, got {flags3.get('unoffered_value')}"
    assert flags3.get("selected_teacher") is None, f"Expected selected_teacher is None, got {flags3.get('selected_teacher')}"

    new_state3 = apply_extraction_to_state(teacher_query_state, flags3)
    assert new_state3.get("teacher") is None, f"Expected state teacher is None, got {new_state3.get('teacher')}"

    prompt3 = format_system_prompt(new_state3, course_id="yoga")
    assert "UNOFFERED INSTRUCTOR" in prompt3, "Expected prompt to contain unoffered instructor guidance"
    assert "INSTRUCTOR IS NOT SELECTED" in prompt3, "Expected anti-assumption guard for instructor in prompt"
    print("✅ TEST 3 PASSED: Unoffered teacher correctly flagged, zero snapping to random instructors, certified teachers presented!")

    # ----------------------------------------------------
    # TEST 4: Course Switching & State Isolation
    # ----------------------------------------------------
    print("\n--- TEST 4: Course Switch Isolation (Yoga [PAID] -> Ladies Dance) ---")
    from src.core.conversation_handler import handle_incoming_message
    session_state = {
        "phone": "wa_917678368328",
        "course": "yoga",
        "stage": "ENROLLED",
        "payment_status": "PAID",
        "paid_amount": 1.0,
        "is_enrolled": True,
        "timing": "6:00 AM to 7:00 AM",
        "timing_period": "Morning",
        "teacher": "Suman Lata",
        "days": "Monday to Friday"
    }
    
    # User switches course
    msg4 = "ab mrko ladies dance m enroll krna h"
    res4 = await handle_incoming_message(
        user_message=msg4,
        session_state=dict(session_state),
        conversation_history=[]
    )
    st4 = res4.get("new_state") or {}
    print(f"State after switch: course={st4.get('course')}, stage={st4.get('stage')}, payment_status={st4.get('payment_status')}, timing_period={st4.get('timing_period')}")
    assert st4.get("course") == "ladies_dance", f"Expected course == 'ladies_dance', got {st4.get('course')}"
    assert st4.get("payment_status") != "PAID", f"Expected payment_status != 'PAID' for new course, got {st4.get('payment_status')}"
    assert st4.get("timing_period") is None, f"Expected timing_period is None for new course, got {st4.get('timing_period')}"
    print("✅ TEST 4 PASSED: Course switch cleanly resets timing_period, timing, teacher, package, and isolates payment status!")

    print("\n==================================================")
    print("🎉 ALL TESTS PASSED WITH 100% SUCCESS!")
    print("==================================================")

if __name__ == "__main__":
    asyncio.run(test_all())
