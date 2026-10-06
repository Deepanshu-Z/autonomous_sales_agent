"""
test_40_questions_benchmark.py — 40-Question Real AI Benchmark Across All 4 Courses.
Tests 10 questions per course (timings, teacher mapping, pricing, GST, demo videos,
trial classes, not-offered policies) and matches responses with actual course JSON data.
"""

import os
import sys
import json
import asyncio
import re
from typing import Dict, Any, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from langchain_core.messages import SystemMessage, HumanMessage
from src.core.conversation_handler import (
    handle_incoming_message,
    _ensure_app_website_links,
)
from src.core import kb_loader
from src.state.chat_state import get_default_state


# Define 10 test questions per course with factual assertions against course JSON
BENCHMARK_SUITE = {
    "yoga": [
        {
            "id": "Y1",
            "category": "All Timings",
            "question": "What are all the batch timings available for yoga classes?",
            "expected_keywords": ["5:00", "6:00", "7:00", "8:00", "10:00", "12:00", "4:00", "5:00", "6:00", "7:00"],
            "forbidden_keywords": ["9:00 AM to 10:00 AM", "2:00 PM to 3:00 PM", "3:00 PM to 4:00 PM"],
        },
        {
            "id": "Y2",
            "category": "Teacher Mapping Morning",
            "question": "Who teaches the morning 7:00 AM yoga batch and what is their experience?",
            "expected_keywords": ["Mradula"],
            "forbidden_keywords": ["Parul", "Madhuri", "Aditi"],
        },
        {
            "id": "Y3",
            "category": "Teacher Mapping Evening",
            "question": "Who is the instructor for the 6:00 PM evening yoga batch?",
            "expected_keywords": ["Suman Lata"],
            "forbidden_keywords": ["Sonali mam", "Vibha"],
        },
        {
            "id": "Y4",
            "category": "All Fees",
            "question": "What are all the package durations and fees for yoga?",
            "expected_keywords": ["700", "600", "900", "1200"],
            "forbidden_keywords": ["2100", "3800"],
        },
        {
            "id": "Y5",
            "category": "GST Slab",
            "question": "Is GST included in the yoga course fee, or will extra tax be charged?",
            "expected_keywords": ["5% GST", "5%"],
            "forbidden_keywords": ["18% GST"],
        },
        {
            "id": "Y6",
            "category": "Demo Videos Overview",
            "question": "Can I watch demo videos of yoga instructors?",
            "expected_keywords": ["Suman Lata", "Mradula", "Priya Mathur", "http"],
            "forbidden_keywords": ["Parul", "Madhuri", "Aditi Vyas"],
        },
        {
            "id": "Y7",
            "category": "Specific Demo Video",
            "question": "Please give me the demo video for Suman Lata.",
            "expected_keywords": ["youtu.be/IiVVdu4NkwI"],
            "forbidden_keywords": [],
        },
        {
            "id": "Y8",
            "category": "Missing Demo Fallback",
            "question": "Do you have a demo video for Sonali Dhote or Prachi?",
            "expected_keywords": ["no recorded demo", "trial"],
            "forbidden_keywords": [],
        },
        {
            "id": "Y9",
            "category": "Trial Booking Steps",
            "question": "How can I book a free trial class for yoga?",
            "expected_keywords": ["trial", "App"],
            "forbidden_keywords": ["confirm ho gaya", "has been booked"],
        },
        {
            "id": "Y10",
            "category": "Not Offered Policy",
            "question": "Can I get recordings of live yoga classes if I miss them?",
            "expected_keywords": ["not provide recordings", "live"],
            "forbidden_keywords": ["recordings are available", "we provide recordings"],
        },
    ],

    "kathak": [
        {
            "id": "K1",
            "category": "All Timings",
            "question": "What are the available class timings for Kathak?",
            "expected_keywords": ["4:00 PM", "5:00 PM", "6:00 PM", "7:00 PM", "8:00 PM"],
            "forbidden_keywords": ["7:00 AM", "6:00 AM", "8:00 AM"],
        },
        {
            "id": "K2",
            "category": "Teacher Mapping 4 PM",
            "question": "Who teaches the 4:00 PM Kathak batch?",
            "expected_keywords": ["Madhuri"],
            "forbidden_keywords": ["Mradula", "Parul"],
        },
        {
            "id": "K3",
            "category": "Shared Slot 6 PM",
            "question": "Which teachers take the 6:00 PM to 7:00 PM Kathak classes?",
            "expected_keywords": ["Madhuri", "Vibha", "Kratika"],
            "forbidden_keywords": ["Suman Lata"],
        },
        {
            "id": "K4",
            "category": "All Fees",
            "question": "What are the fees for Kathak classes for all packages?",
            "expected_keywords": ["700", "1500", "2500", "4000"],
            "forbidden_keywords": [],
        },
        {
            "id": "K5",
            "category": "GST Slab",
            "question": "What is the GST percentage on Kathak dance classes?",
            "expected_keywords": ["18%"],
            "forbidden_keywords": ["5% GST"],
        },
        {
            "id": "K6",
            "category": "Demo Videos Overview",
            "question": "Can I see demo videos of Kathak teachers?",
            "expected_keywords": ["Madhuri", "http"],
            "forbidden_keywords": ["Suman Lata", "Mradula", "Priya Mathur"],
        },
        {
            "id": "K7",
            "category": "Specific Demo Video",
            "question": "Send me Ms. Madhuri's Kathak demo video.",
            "expected_keywords": ["youtu.be/t-XO2enPGwM"],
            "forbidden_keywords": [],
        },
        {
            "id": "K8",
            "category": "Weekend Batch",
            "question": "Are there weekend Kathak batches on Saturday and Sunday?",
            "expected_keywords": ["Saturday", "Sunday"],
            "forbidden_keywords": ["no weekend", "not available on weekend"],
        },
        {
            "id": "K9",
            "category": "Trial Booking Steps",
            "question": "Can I try a Kathak class first before paying?",
            "expected_keywords": ["trial", "Sensationz App"],
            "forbidden_keywords": ["confirm ho gaya", "has been booked"],
        },
        {
            "id": "K10",
            "category": "Not Offered Policy",
            "question": "Do you offer offline in-person Kathak classes or home tuition?",
            "expected_keywords": ["online", "offline"],
            "forbidden_keywords": ["we offer offline", "home tuition is available"],
        },
    ],

    "ladies_dance": [
        {
            "id": "D1",
            "category": "All Timings",
            "question": "What are the timings for Ladies Bollywood Dance classes?",
            "expected_keywords": ["11:00 AM", "12:00 PM", "1:00 PM", "4:00 PM", "5:00 PM", "6:00 PM", "8:00 PM"],
            "forbidden_keywords": ["7:00 AM"],
        },
        {
            "id": "D2",
            "category": "Teacher Mapping 1 PM",
            "question": "Who teaches the 1:00 PM afternoon dance batch?",
            "expected_keywords": ["Lokesh"],
            "forbidden_keywords": ["Mradula", "Madhuri"],
        },
        {
            "id": "D3",
            "category": "Teacher Mapping Aditi",
            "question": "What batch timings does Aditi Vyas teach in Ladies Dance?",
            "expected_keywords": ["11:00 AM", "12:00 PM", "6:00 PM", "8:00 PM"],
            "forbidden_keywords": ["1:00 PM to 2:00 PM"],
        },
        {
            "id": "D4",
            "category": "All Fees",
            "question": "What is the fee structure for Ladies Bollywood Dance?",
            "expected_keywords": ["500", "600", "900", "1200"],
            "forbidden_keywords": [],
        },
        {
            "id": "D5",
            "category": "GST Slab",
            "question": "Will tax be added to the dance course fees?",
            "expected_keywords": ["18%"],
            "forbidden_keywords": ["5% GST"],
        },
        {
            "id": "D6",
            "category": "Demo Videos Overview",
            "question": "Can you share demo videos for Ladies Bollywood Dance teachers?",
            "expected_keywords": ["Aditi", "instagram.com/reel"],
            "forbidden_keywords": ["Suman Lata", "Mradula", "Priya Mathur"],
        },
        {
            "id": "D7",
            "category": "Missing Demo Teacher",
            "question": "Do you have a demo video for Lokesh Kumar sir?",
            "expected_keywords": ["no recorded demo", "Lokesh"],
            "forbidden_keywords": [],
        },
        {
            "id": "D8",
            "category": "Class What to Keep Ready",
            "question": "What should I wear or keep ready for the dance class?",
            "expected_keywords": ["comfortable", "water"],
            "forbidden_keywords": [],
        },
        {
            "id": "D9",
            "category": "Trial Booking Steps",
            "question": "How do I take a free trial for Ladies Dance?",
            "expected_keywords": ["trial", "Profile", "Trial Links"],
            "forbidden_keywords": ["confirm ho gaya", "booked"],
        },
        {
            "id": "D10",
            "category": "Ladies Only Policy",
            "question": "Can men join this Ladies Bollywood Dance class?",
            "expected_keywords": ["female", "ladies"],
            "forbidden_keywords": [],
        },
    ],

    "face_yoga": [
        {
            "id": "FY1",
            "category": "All Timings",
            "question": "What are all the batch timings for Face Yoga?",
            "expected_keywords": ["7:00 AM", "12:00 PM", "4:00 PM", "6:00 PM"],
            "forbidden_keywords": ["5:00 AM", "8:00 AM to 9:00 AM", "10:00 AM"],
        },
        {
            "id": "FY2",
            "category": "Teacher Mapping Parul",
            "question": "Who teaches the 7:00 AM morning Face Yoga class?",
            "expected_keywords": ["Parul"],
            "forbidden_keywords": ["Mradula", "Suman Lata", "Madhuri"],
        },
        {
            "id": "FY3",
            "category": "Teacher Mapping Sonali",
            "question": "Who takes the 12:00 PM afternoon Face Yoga batch?",
            "expected_keywords": ["Sonali"],
            "forbidden_keywords": ["Nidhi", "Aditi"],
        },
        {
            "id": "FY4",
            "category": "All Fees",
            "question": "What is the price of Face Yoga for 1 month and 3 months?",
            "expected_keywords": ["300", "1000"],
            "forbidden_keywords": [],
        },
        {
            "id": "FY5",
            "category": "GST Slab",
            "question": "How much GST is applicable on Face Yoga?",
            "expected_keywords": ["5%"],
            "forbidden_keywords": ["18% GST"],
        },
        {
            "id": "FY6",
            "category": "Demo Videos Overview",
            "question": "Can I see demo videos for Face Yoga instructors?",
            "expected_keywords": ["Parul", "Sonali", "http"],
            "forbidden_keywords": ["Suman Lata", "Mradula", "Priya Mathur"],
        },
        {
            "id": "FY7",
            "category": "Parul Demo Video",
            "question": "Send me the demo video for Parul mam Face Yoga.",
            "expected_keywords": ["youtu.be/PzwrL2P8mYQ"],
            "forbidden_keywords": [],
        },
        {
            "id": "FY8",
            "category": "Tools Requirement",
            "question": "Do I need to buy a jade roller or gua sha for Face Yoga?",
            "expected_keywords": ["tools"],
            "forbidden_keywords": ["you must buy", "mandatory"],
        },
        {
            "id": "FY9",
            "category": "Medical Disclaimer",
            "question": "Can you guarantee that Face Yoga will cure all my wrinkles in 1 month?",
            "expected_keywords": ["vary", "treatment"],
            "forbidden_keywords": ["100% guarantee", "we promise complete cure"],
        },
        {
            "id": "FY10",
            "category": "Not Offered Policy",
            "question": "Can I get 1-on-1 private coaching for Face Yoga?",
            "expected_keywords": ["not offer", "group", "1-on-1"],
            "forbidden_keywords": ["we offer private", "private coaching is available"],
        },
    ],
}


async def run_single_benchmark_turn(course_id: str, question: str) -> Dict[str, str]:
    """Invokes the actual AI generation pipeline for a question under a given course context."""
    state = get_default_state("+919876543210")
    state["course"] = course_id

    result = await handle_incoming_message(
        user_message=question,
        session_state=state,
        conversation_history=[],
        course_id=course_id,
    )

    reply = result.get("reply", "")
    followup = result.get("followup") or ""

    user_asked_demo = bool(re.search(r"\b(demo|video|sample|preview)\b", question, re.IGNORECASE))
    cleaned_reply = _ensure_app_website_links(reply, user_asked_for_links=False, allow_demo_links=user_asked_demo)

    return {
        "reply": cleaned_reply,
        "followup": followup,
        "full_text": f"{cleaned_reply} {followup}",
    }


def evaluate_response(response_text: str, test_def: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Evaluates the AI response against expected keywords and forbidden keywords."""
    failures = []
    text_lower = response_text.lower()

    # Check expected keywords (at least all or major keywords must appear)
    for kw in test_def["expected_keywords"]:
        if kw.lower() not in text_lower:
            failures.append(f"Missing expected keyword: '{kw}'")

    # Check forbidden keywords (none must appear)
    for f_kw in test_def["forbidden_keywords"]:
        if f_kw.lower() in text_lower:
            failures.append(f"Contained forbidden keyword: '{f_kw}'")

    passed = len(failures) == 0
    return passed, failures


async def run_full_benchmark():
    print("=" * 80)
    print("🎯 RUNNING 40-QUESTION BENCHMARK ACROSS ALL DIGITIZED COURSES")
    print("=" * 80)

    total_tests = 0
    total_passed = 0
    course_scores = {}

    for course_id, questions in BENCHMARK_SUITE.items():
        course_name = course_id.upper()
        print(f"\n{'─' * 50}")
        print(f"📘 COURSE BENCHMARK: {course_name} (10 Questions)")
        print(f"{'─' * 50}")

        passed_for_course = 0

        for idx, q_info in enumerate(questions, 1):
            q_id = q_info["id"]
            cat = q_info["category"]
            q_text = q_info["question"]

            print(f"[{q_id}] ({cat}) Q: {q_text}", flush=True)
            res = await run_single_benchmark_turn(course_id, q_text)
            reply = res["reply"]

            passed, reasons = evaluate_response(res["full_text"], q_info)
            total_tests += 1

            if passed:
                passed_for_course += 1
                total_passed += 1
                print(f"    ✅ PASS | AI Reply: {reply[:110]}...", flush=True)
            else:
                print(f"    ❌ FAIL | Reasons: {', '.join(reasons)}", flush=True)
                print(f"    AI Reply: {reply}", flush=True)

            await asyncio.sleep(0.2)

        course_scores[course_name] = f"{passed_for_course}/10"
        print(f"\n📊 {course_name} Result: {passed_for_course}/10 Passed", flush=True)

    print("\n" + "=" * 80, flush=True)
    print("🏆 FINAL BENCHMARK SUMMARY", flush=True)
    print("=" * 80, flush=True)
    for cname, score in course_scores.items():
        print(f"  • {cname:15}: {score}", flush=True)
    print(f"\nTotal Passed: {total_passed}/{total_tests} ({(total_passed/total_tests)*100:.1f}%)", flush=True)
    print("=" * 80, flush=True)


if __name__ == "__main__":
    from typing import Tuple
    asyncio.run(run_full_benchmark())
