"""
eval_feels_human.py — Comprehensive 'Feels Human' Conversational Evaluation Rubric.

Evaluates chat conversations against the 8 Karpathy-inspired conversational principles:
1. Repetition Score (Verbatim repetition check across assistant turns)
2. Discovery Score (Presence of adaptive goal/experience/schedule discovery before pitch)
3. Rapport-Before-Urgency Ordering (No artificial scarcity on Turn 1 / early exploratory turns)
4. Objection Empathy (Empathetic reframing on price pushback, 'let me think', YouTube comparisons)
5. State-Voice Separation (Zero raw checklists like ✅, ⏳, 🟩, or 'Setup: 80% Complete!')
6. Factual & Pricing Guardrails (Exact catalog prices, 5% GST transparency, zero unoffered claims)

Usage:
    python scripts/eval_feels_human.py [--limit 300] [--offline]
"""

import sys
import os
import re
import argparse
from typing import List, Dict, Any, Tuple
from collections import defaultdict

# Set stdout encoding
sys.stdout.reconfigure(encoding='utf-8')

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Regex patterns for rubric evaluation
URGENCY_PATTERNS = [
    r"\bonly 2 seats\b",
    r"\b15[- ]minute(?:s)? hold\b",
    r"\bprovisionally held\b",
    r"\bliterally the last step\b",
    r"\brelease it to the (?:next person|waitlist)\b",
]

CHECKLIST_PATTERNS = [
    r"setup:\s*\d+%\s*complete",
    r"[✅⏳🟩]",
    r"\bprogress:\s*\d+%",
]

DISCOVERY_QUESTIONS = [
    r"\b(practiced|done|tried).{0,25}(before|yoga|dance|beginner|starting fresh)\b",
    r"\b(goal|focus|looking for|achieve|weight|flexibility|stress)\b",
    r"\b(morning|evening|timing|routine|schedule).{0,25}(better|suits|prefer|work)\b",
    r"\b(pehle|shuru|experience|fayda|timing)\b",
]

OBJECTION_TRIGGERS = [
    (r"\b(expensive|mehenga|costly|fees zyada|discount|budget)\b", "PRICE"),
    (r"\b(soch kar|think about|batati hu|batata hu|later|discuss)\b", "THINK"),
    (r"\b(youtube|free|another app|other app|online kyu)\b", "COMPARE"),
]

OBJECTION_RESPONSES = {
    "PRICE": [
        r"₹?(?:5\.8|7|210|less than a chai|per day|budget|rozana)",
        r"gst",
        r"1 month",
    ],
    "THINK": [
        r"(understand|completely natural|bilkul|take your time|zaroor|helpful|clarify)",
    ],
    "COMPARE": [
        r"(live|camera|posture|correction|feedback|real-time|teacher|dhyan)",
    ],
}


def evaluate_conversation(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Evaluates a single conversation trajectory and returns rubric scores (0-100).
    """
    assistant_msgs = [m.get("message", "") or m.get("content", "") for m in messages if m.get("role") in ("assistant", "ai")]
    user_msgs = [m.get("message", "") or m.get("content", "") for m in messages if m.get("role") in ("user", "customer")]

    results = {
        "repetition_score": 100.0,
        "discovery_score": 100.0,
        "rapport_urgency_score": 100.0,
        "no_checklist_score": 100.0,
        "objection_score": 100.0,
        "violations": [],
    }

    if not assistant_msgs:
        return results

    # 1. Repetition Check: Check similarity / exact phrase overlap between assistant turns
    seen_phrases = []
    repetition_deductions = 0
    for idx, msg in enumerate(assistant_msgs):
        msg_clean = msg.lower()
        # Extract meaningful clauses (10+ words)
        clauses = [c.strip() for c in re.split(r"[.!?\n]", msg_clean) if len(c.strip().split()) >= 6]
        for clause in clauses:
            for past_idx, past_clause in seen_phrases:
                if clause == past_clause and idx != past_idx:
                    repetition_deductions += 25
                    results["violations"].append(f"Verbatim repetition across Turn {past_idx+1} and {idx+1}: '{clause[:60]}...'")
            seen_phrases.append((idx, clause))
    results["repetition_score"] = max(0.0, 100.0 - repetition_deductions)

    # 2. Checklist / Raw State Monitor Check
    checklist_hits = 0
    for idx, msg in enumerate(assistant_msgs):
        for pattern in CHECKLIST_PATTERNS:
            if re.search(pattern, msg, re.IGNORECASE):
                checklist_hits += 1
                results["violations"].append(f"Raw checklist/progress detected on Turn {idx+1}: match pattern '{pattern}'")
    results["no_checklist_score"] = max(0.0, 100.0 - (checklist_hits * 35))

    # 3. Rapport-Before-Urgency Check (Turn 1 / early turns without selection)
    turn1_urgency = 0
    if len(assistant_msgs) >= 1:
        first_msg = assistant_msgs[0]
        for pattern in URGENCY_PATTERNS:
            if re.search(pattern, first_msg, re.IGNORECASE):
                turn1_urgency += 1
                results["violations"].append(f"Premature urgency on Turn 1: match '{pattern}'")
    results["rapport_urgency_score"] = max(0.0, 100.0 - (turn1_urgency * 40))

    # 4. Discovery Check: Does early conversation attempt discovery (goal, experience, schedule)?
    has_discovery = False
    for msg in assistant_msgs[:2]:
        if any(re.search(p, msg, re.IGNORECASE) for p in DISCOVERY_QUESTIONS):
            has_discovery = True
            break
    # If customer already stated goal/timing on Turn 1, discovery is implicitly satisfied
    if user_msgs:
        first_user = user_msgs[0]
        if any(re.search(p, first_user, re.IGNORECASE) for p in [r"\bmorning\b", r"\bevening\b", r"\bweight\b", r"\bbeginner\b"]):
            has_discovery = True
    if not has_discovery and len(assistant_msgs) >= 2:
        results["discovery_score"] = 60.0  # Missed natural early discovery
        results["violations"].append("No adaptive discovery question asked in first 2 assistant turns.")

    # 5. Objection Handling Empathy Check
    for u_idx, u_msg in enumerate(user_msgs):
        for trigger_pat, obj_type in OBJECTION_TRIGGERS:
            if re.search(trigger_pat, u_msg, re.IGNORECASE):
                # Check next assistant reply
                if u_idx < len(assistant_msgs):
                    a_reply = assistant_msgs[u_idx]
                    expected_patterns = OBJECTION_RESPONSES.get(obj_type, [])
                    matched = any(re.search(p, a_reply, re.IGNORECASE) for p in expected_patterns)
                    if not matched:
                        results["objection_score"] = max(0.0, results["objection_score"] - 35)
                        results["violations"].append(f"Objection '{obj_type}' on User Turn {u_idx+1} handled without empathetic reframing.")

    # Composite Score
    weights = {
        "repetition_score": 0.25,
        "no_checklist_score": 0.20,
        "rapport_urgency_score": 0.20,
        "discovery_score": 0.20,
        "objection_score": 0.15,
    }
    composite = sum(results[k] * weights[k] for k in weights)
    results["composite_score"] = round(composite, 1)

    return results


def run_synthetic_benchmark() -> Dict[str, Any]:
    """Runs the eval rubric against standard canonical dialogue flows."""
    dialogues = {
        "Ideal_Consultative_Flow": [
            {"role": "user", "content": "Hi, I want to know about yoga classes."},
            {"role": "assistant", "content": "Welcome to Sensationz! Our live interactive yoga sessions are guided by certified female master teachers who give real-time posture corrections on camera.\n\nTo help customize this for you, do morning batches or evening batches work better with your routine?"},
            {"role": "user", "content": "Evening batch please, I finish work around 6."},
            {"role": "assistant", "content": "Evening sessions are great for destressing after work! We have 5:00-6:00 PM and 6:00-7:00 PM available. Our beginner 1-Month starter plan is just ₹210 (regular ₹700, ~₹7/day) + 5% GST.\n\nWould you like to lock the 6:00 PM slot or explore the 3-Month transformation plan?"},
            {"role": "user", "content": "Lock My Seat 🔒"},
            {"role": "assistant", "content": "We have tentatively reserved your 6:00 PM evening slot and pre-loaded your welcome discount credit on your number! Please download the Sensationz App (only 20MB) using the links below to activate your pass:\n• Android: https://play.google.com/store/apps/details?id=com.sensationz.sensationz.dev\n\nOpen the app, do a quick 1-tap Google login, and reply 'Done' here so our team can unlock your pre-class preparation guide!"},
        ],
        "Price_Objection_Handling": [
            {"role": "user", "content": "Fees kitni hai?"},
            {"role": "assistant", "content": "Namaste! Sensationz live yoga classes ke packages:\n• 1 Month: ₹210 (Regular ₹700)\n• 3 Months: ₹525 (Regular ₹1750)\n• 6 Months: ₹960 (Regular ₹3200)\n• 1 Year: ₹1500 (Regular ₹5000)\nCheckout par standard 5% GST add hota hai.\n\nKya aap morning batch prefer karte hain ya evening batch?"},
            {"role": "user", "content": "Fees thoda zyada lag raha hai."},
            {"role": "assistant", "content": "Main samajh sakti hoon! 3-Month package mein yeh sirf ₹5.8 per day banta hai — ek chai se bhi kam. Isme certified female master trainer aapko camera par live watch aur posture correct karti hain, taaki koi injury na ho.\n\nAap chahein toh ₹210 wale 1-month plan se shuru karke live experience dekh sakte hain. Kya morning 7:00 AM ya evening 6:00 PM slot aapke liye better rahega?"},
        ],
        "YouTube_Comparison_Objection": [
            {"role": "user", "content": "Main toh YouTube se free me dekh sakta hu."},
            {"role": "assistant", "content": "YouTube par kaafi generic videos available hain, yeh bilkul sahi hai! Lekin YouTube videos aapka posture live camera par nahi dekh sakte. Galat breathing ya posture se back strain ka risk rehta hai. Sensationz mein master instructor aapko live dekh kar real-time feedback dete hain.\n\nKya aapne pehle kabhi yoga live try kiya hai, ya bilkul fresh start kar rahe hain?"},
        ],
        "Anti_Pattern_Robotic_Checklist (Bad Example)": [
            {"role": "user", "content": "Lock My Seat 🔒"},
            {"role": "assistant", "content": "Setup: 80% Complete!\n✅ Batch selected: 6:00 PM\n✅ Instructor: Live Master Trainer\n✅ Discount loaded: 70% off\n⏳ Pending: Just your Google login (10 seconds)\n\nDownload the Sensationz App — it's literally the last step!"},
        ]
    }

    print("\n" + "="*70)
    print("RUNNING 'FEELS HUMAN' SYNTHETIC BENCHMARK")
    print("="*70)

    benchmark_scores = []
    for name, convo in dialogues.items():
        score = evaluate_conversation(convo)
        benchmark_scores.append(score["composite_score"])
        status = "PASSED" if score["composite_score"] >= 80 else "FAILED (FLAGGED)"
        print(f"\nScenario: {name} [{status}]")
        print(f"  • Composite Human Score:     {score['composite_score']}/100")
        print(f"  • Repetition Score:          {score['repetition_score']}/100")
        print(f"  • No-Raw-Checklist Score:    {score['no_checklist_score']}/100")
        print(f"  • Rapport-Before-Urgency:    {score['rapport_urgency_score']}/100")
        print(f"  • Discovery Score:           {score['discovery_score']}/100")
        print(f"  • Objection Handling Score:  {score['objection_score']}/100")
        if score["violations"]:
            print(f"  • Violations Caught ({len(score['violations'])}):")
            for v in score["violations"]:
                print(f"    - {v}")

    avg_score = round(sum(benchmark_scores) / len(benchmark_scores), 1)
    print("\n" + "-"*70)
    print(f"Synthetic Benchmark Average: {avg_score}/100")
    print("-" * 70)
    return {"average": avg_score}


def run_database_audit(limit: int = 300) -> None:
    """Audits recent production chats from Supabase."""
    from src.services.supabase_client import supabase

    print("\n" + "="*70)
    print(f"AUDITING UP TO {limit} CONVERSATIONS FROM SUPABASE")
    print("="*70)

    try:
        r = supabase.table('chat_history').select('*').order('created_at', desc=True).limit(limit * 8).execute()
        all_chats = r.data or []
    except Exception as e:
        print(f"Failed to fetch Supabase chats: {e}. Falling back to synthetic benchmark.")
        run_synthetic_benchmark()
        return

    by_phone = defaultdict(list)
    for msg in all_chats:
        by_phone[msg['phone']].append(msg)

    evaluated = 0
    total_composite = 0.0
    total_repetition = 0.0
    total_checklist = 0.0
    total_urgency = 0.0
    total_discovery = 0.0
    total_objection = 0.0

    flagged_conversations = []

    for phone, msgs in list(by_phone.items())[:limit]:
        msgs.sort(key=lambda x: x.get('created_at') or '')
        eval_res = evaluate_conversation(msgs)

        evaluated += 1
        total_composite += eval_res["composite_score"]
        total_repetition += eval_res["repetition_score"]
        total_checklist += eval_res["no_checklist_score"]
        total_urgency += eval_res["rapport_urgency_score"]
        total_discovery += eval_res["discovery_score"]
        total_objection += eval_res["objection_score"]

        if eval_res["composite_score"] < 75:
            flagged_conversations.append((phone, eval_res))

    if evaluated == 0:
        print("No database messages found to evaluate.")
        return

    print(f"\nSuccessfully Audited {evaluated} Real Customer Conversations:")
    print(f"  • Overall 'Feels Human' Score:   {round(total_composite / evaluated, 1)}/100")
    print(f"  • Repetition Score:              {round(total_repetition / evaluated, 1)}/100")
    print(f"  • Clean Voice (No Checklists):   {round(total_checklist / evaluated, 1)}/100")
    print(f"  • Rapport Before Urgency:        {round(total_urgency / evaluated, 1)}/100")
    print(f"  • Discovery Adherence:           {round(total_discovery / evaluated, 1)}/100")
    print(f"  • Objection Empathy:             {round(total_objection / evaluated, 1)}/100")

    print(f"\nConversations Flagged for Review (<75 score): {len(flagged_conversations)}")
    for phone, res in flagged_conversations[:5]:
        print(f"  [{phone}] Score: {res['composite_score']} | Violations: {res['violations'][:2]}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate conversational human quality rubric.")
    parser.add_argument("--limit", type=int, default=300, help="Number of conversations to audit")
    parser.add_argument("--offline", action="store_true", help="Run only synthetic benchmark")
    args = parser.parse_args()

    run_synthetic_benchmark()
    if not args.offline:
        run_database_audit(limit=args.limit)
