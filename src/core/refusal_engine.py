"""
refusal_engine.py — Reusable Course Refusal, Risk & Solution Engine.

Evaluates customer refusals/disinterest:
1. Detects if user declined/refused course enrollment.
2. If reason is NOT provided, politely asks for the reason.
3. If reason IS provided:
   - Checks if it involves legal, dispute, harassment, safety, or compliance risk.
   - Checks if our knowledge base contains a viable solution.
   - If risk exists OR no solution exists:
     -> Immediately generates an acknowledgment telling the user their query was heard
        and an agent is being assigned, and signals agent escalation.
   - If a viable solution exists:
     -> Generates an empathetic, knowledge-backed solution in the user's language.
"""

import os
import json
from typing import Dict, Any, Optional, List
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from src.core import kb_loader

load_dotenv()

_refusal_llm = None

def _get_refusal_llm():
    # BUG5 FIX: Mirror the OpenRouter-first pattern used by the rest of the app.
    global _refusal_llm
    if _refusal_llm is None:
        openrouter_key = os.getenv("OPENROUTER_API_KEY")
        if openrouter_key:
            model_name = os.getenv("OPENROUTER_REPLY_MODEL", "openai/gpt-4o-mini")
            _refusal_llm = ChatOpenAI(
                model=model_name,
                api_key=openrouter_key,
                base_url="https://openrouter.ai/api/v1",
                temperature=0.2,
                timeout=15,
                max_retries=1,
            )
        else:
            _refusal_llm = ChatOpenAI(
                model=os.getenv("OPENAI_MODEL", "gpt-4o"),
                api_key=os.getenv("OPENAI_API_KEY"),
                temperature=0.2,
                timeout=15,
                max_retries=1,
            )
    return _refusal_llm


_SYSTEM_PROMPT = """You are the intelligent Refusal & Risk Evaluation Engine for Sensationz performing arts & wellness academy.
Your task is to analyze incoming customer messages for course refusal, disinterest, objections, risks, or feedback.

COURSE OFFERINGS & POLICIES SUMMARY:
- All classes are 100% ONLINE LIVE INTERACTIVE via App/Website (Mon-Fri).
- Physical branch addresses are ADMINISTRATIVE OFFICES ONLY. Offline classes or physical studio visits are NOT offered.
- Cash or offline payments are STRICTLY NOT ACCEPTED.
- Course fees once paid are non-refundable.
- Offerings: 10 fixed batch timing slots available (ranging 5:00 AM to 8:00 PM) — students choose ONE fixed batch at enrollment; batch can be changed after 1 month. Flexible/on-demand timings are NOT offered. Recorded sessions in app, starter packages, up to 3 free live trial classes, certified instructors, beginner-friendly pace.


ANALYSIS CRITERIA:
1. REFUSAL DETECTION:
   - Is the customer refusing, declining, dropping out, or showing disinterest in taking the course/classes?
   - Examples: "Not interested", "Nahi chahiye", "No thanks", "Rehne do", "Cancel", "Too expensive", "I don't have time", "Nahi lena".
   - Note: If `Refusal Stage` is 1, the customer is now responding with their reason for earlier refusal — treat their reply as the reason even if it doesn't sound like a new refusal.

2. REASON PRESENCE:
   - Did the customer specify WHY they are refusing, hesitating, or postponing?
   - If they only said "not interested", "nahi chahiye", "no" without context -> `has_reason` = false.
   - If they gave ANY explanation or current situation:
     • Course objections: "fees are too high", "timing conflict", "busy schedule", "want offline classes", "scam company", "had surgery"
     • Situational / temporal unavailability: "office mein hu", "at work", "in office", "driving", "in meeting", "busy right now", "traveling", "exams chal rahe hain", "bimar hu"
     -> ALWAYS set `has_reason` = true and extract the situation as `extracted_reason`.

3. RISK & LEGAL EVALUATION — MANDATORY CHECK (read every sub-point):
   - Does the message involve ANY of the following?
     • Legal threats, court, police, lawyer, consumer complaint, fraud/scam allegations, or chargeback threats?
     • REFUND DEMANDS: Customer says "refund chahiye", "paise wapas karo", "money back", "return my payment", "mujhe paise wapas chahiye" — ALWAYS set `has_risk_or_legal` = true and `risk_category` = "PAYMENT_DISPUTE".
     • Financial/payment disputes or unauthorized debit accusations?
     • Harassment, staff misconduct, or abusive incident?
     • Acute/severe medical hazards where taking exercise classes is unsafe?
   - CRITICAL: A customer who has ALREADY PAID and is asking for a refund is NOT a normal disinterest case. It MUST be treated as `has_risk_or_legal` = true and escalated immediately.
   - If YES to any -> `has_risk_or_legal` = true.

4. SOLUTION VIABILITY:
   - Can our system/knowledge base legitimately solve the customer's reason?
     • Solvable: Budget/price (free trial, discount coupon, 1-month starter pack), schedule/timing (10 fixed batch slots 5AM–8PM to choose from, batch switch allowed after 1 month, app recordings), beginner fear (interactive beginner classes, certified guidance), trial/trust (free live trial, demo videos).
     • Temporary / Situational Unavailability: Customer is currently at office/work, in a meeting, driving, traveling, or temporarily occupied. This is SOLVABLE by respecting their time, acknowledging their schedule politely with zero pressure, and letting them know they can connect whenever free (or we can check in later). Set `solution_viable` = true.
     • NOT Solvable: Customer insists on physical offline in-person studio classes, demands 1-on-1 private home tutors, demands cash payment at physical office, refund demand, or has unserviceable demands.
   - If the request cannot be fulfilled by our offerings -> `solution_viable` = false.

DECISION OUTCOMES:
- If NOT a refusal (and not awaiting reason) -> `resolution_type` = "NOT_A_REFUSAL".

1. HIGH RISK / LEGAL / HARASSMENT / ANGER (IMMEDIATE AGENT ESCALATION):
   - If the customer expresses legal threats, financial/chargeback disputes, harassment, scam allegations, or direct anger/accusations (e.g. "u r lier", "jhuth bola", "scam company", "police complaint", "fraud", "consumer court"):
     -> `resolution_type` = "ESCALATE_TO_AGENT"
     -> GENERATE a `reply_message` that: sincerely apologizes for the frustration/miscommunication, reassures them we take their concern seriously, and informs them our senior support specialist is stepping in right away.
     -> `escalation_reason`: English summary of the risk/complaint for the human agent.
     -> Example tone: "I'm really sorry to hear about your experience. Please know we take this very seriously. Our senior support specialist will reach out to you directly and very shortly to resolve this. 🙏"

2. PERSISTENT REFUSAL (AFTER SOLUTION ALREADY GIVEN OR REFUSAL STAGE >= 2):
   - If `Refusal Stage` is >= 2 (we already gave a solution/trial/demo), AND the customer:
     • Still says no, not interested, leave it, cancel, nahi chahiye, nahi lena, no thanks, etc.
     • OR compares us to a competitor/cheaper option (e.g. "ABC institute gives yearly for ₹100", "XYZ is cheaper", "other apps are free")
     • OR repeats the same pricing/timing objection despite a solution already being offered
     • OR shows ANY continued hesitation or rejection after our solution — in ANY language or phrasing
     → This is a PERSISTENT REFUSAL regardless of the exact wording. Do NOT offer another solution.
     -> `resolution_type` = "ESCALATE_TO_AGENT"
     -> GENERATE a `reply_message` that: warmly accepts their decision without pressure, thanks them genuinely, and lets them know support is available anytime they need it.
     -> `escalation_reason`: "Customer declined after solution/demo was offered (persistent refusal)".
     -> Example tone: "Completely understood! 😊 Thank you so much for your time. Whenever you feel like exploring again, we're right here. Our coordinator is always available if you need any help!"

3. REFUSAL WITH REASON (OR ANSWERING REFUSAL REASON, OR REFUSAL STAGE == 1):
   - If the customer specifies why they are hesitating/refusing (or is answering our earlier inquiry about why):
     • If reason is UNRESOLVABLE by our offerings (e.g. insists on offline studio, private home tutor, cash payment):
       -> `resolution_type` = "ESCALATE_TO_AGENT"
       -> GENERATE a `reply_message` that: politely explains the offering boundary (e.g. 100% online classes), and lets them know our human coordinator can assist them further.
       -> `escalation_reason`: English summary of the unresolvable request.
     • If reason is SOLVABLE (e.g. budget/fees, schedule/timing, flexible timings, beginner hesitation, fear of pace, doubt about teachers, situational unavailability):
       -> `resolution_type` = "PROVIDE_SOLUTION"
       -> GENERATE a `reply_message` that: warmly and empathetically addresses their specific situation:
          - If temporary situational unavailability (e.g. at office, working, driving, busy right now, in meeting):
            Warmly acknowledge their situation with zero pressure. Let them know they can finish their work and check out the trial or details whenever they are free.
            Example tone (Hinglish): "Bilkul koi baat nahi! 😊 Aap apna office ka kaam complete kar lijiye. Jab bhi aap free hon, tab batayega — hum tab connect kar lenge. Have a productive day at work! 👍"
            Example tone (English): "No problem at all! 😊 Please focus on your work. Feel free to message whenever you are free and we can connect then. Have a great day! 👍"
          - If doubt about schedule, timing flexibility, or missing classes: Warmly explain that live interactive classes run Mon-Fri (1 session/day) across 10 batch slots (5 AM to 8 PM). While day-to-day switching isn't available, they can switch their batch after 1 month, and full recordings are available in the app if they ever miss a session.
          - If doubt about teachers, exercise pace, or beginner guidance: Share YouTube demo video links from ACTIVE COURSE CONTEXT (if available) or free live trial class so they can see the beginner pace firsthand. Do NOT push paid packages.
          - If doubt about fees/pricing: Mention the free live trial class or starter package without being pushy.
          - Keep it inviting, empathetic, and friendly.

4. REFUSAL WITHOUT REASON (FIRST-TIME REFUSAL ONLY, REFUSAL STAGE == 0):
   - ONLY applicable when `Refusal Stage` is 0 AND the customer refused without providing any explanation (e.g. "not interested", "leave it", "nahi chahiye", "no thanks"):
     -> `resolution_type` = "ASK_REASON"
     -> GENERATE a `reply_message` that: warmly and empathetically asks what is holding them back (timing, fees, hesitation, or something else) so we can see if we can help. Never pressure.
   - CRITICAL GUARDRAIL: If `Refusal Stage` >= 1:
     • NEVER output `resolution_type` = "ASK_REASON" under any circumstances. We already asked once.
     • If the customer is answering why (including "office mein hu", "busy hu", etc.), treat it as REFUSAL WITH REASON (`resolution_type` = "PROVIDE_SOLUTION" or "ESCALATE_TO_AGENT").
     • If they repeat pure disinterest or say nothing useful, treat as PERSISTENT REFUSAL (`resolution_type` = "ESCALATE_TO_AGENT").

STRICT GROUNDING ON ACTIVE COURSE DATA (NON-NEGOTIABLE):
- When generating `reply_message` for PROVIDE_SOLUTION:
  • You MUST strictly quote the Active Course Name and its details provided in the ACTIVE COURSE CONTEXT.
  • NEVER assume, invent, or mention 'Yoga' or '₹300' unless the active course is explicitly Yoga.
  • Share YouTube demo videos if present in ACTIVE COURSE CONTEXT.
  • If no active course is known: DO NOT quote fees or specific course names. Warmly ask which course they are exploring.

STRICT LANGUAGE & SCRIPT MATCHING FOR `reply_message`:
- Detect and match the exact language and script of the customer's input (English, Hindi, Hinglish, Marathi, Tamil, Telugu, etc.).
- If Romanized/Latin script is used (e.g. Hinglish), reply using ONLY the Latin alphabet (NEVER output Devanagari or native script).
- Use native script only if the customer explicitly typed in native script.

Respond ONLY with strict JSON:
{
  "is_refusal": bool,
  "has_reason": bool,
  "extracted_reason": string or null,
  "has_risk_or_legal": bool,
  "risk_category": "LEGAL" | "PAYMENT_DISPUTE" | "HARASSMENT" | "UNSAFE_MEDICAL" | "NONE",
  "solution_viable": bool,
  "resolution_type": "ASK_REASON" | "PROVIDE_SOLUTION" | "ESCALATE_TO_AGENT" | "NOT_A_REFUSAL",
  "reply_message": string or null,
  "escalation_reason": string or null
}"""


async def evaluate_refusal_async(
    text: str,
    history: Optional[List[Dict[str, str]]] = None,
    state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Evaluates incoming text for course refusal, risk, and solution feasibility.
    Returns structured decision dictionary. Grounded in active course data.
    """
    state = state or {}
    # BUG1 FIX: `awaiting_refusal_reason` was never written to state by the handler.
    # `refusal_stage == 1` is the canonical signal — use only that.

    # Resolve active course dynamically from state or history (never hardcoded 'yoga')
    course_id = (state.get("course") or state.get("active_course") or "").strip()
    if not course_id and history:
        from src.core import kb_loader
        hist_text = " ".join((t.get("content") or "") for t in history[-6:])
        course_id = kb_loader.detect_course_from_text(hist_text, default_course=None) or ""

    course_context_str = "No active course specified. Do NOT guess fees or course names."
    if course_id:
        from src.core import kb_loader
        course_data = kb_loader.get_course_data(course_id)
        if course_data:
            course_name = course_data.get("course", course_id.title())
            pkgs = course_data.get("pricing", {}).get("packages", [])
            # BUG6 FIX: Pick shortest-duration package as starter, not pkgs[0].
            # Packages may be ordered arbitrarily in the JSON.
            starter = min(pkgs, key=lambda p: p.get("duration_days", 9999)) if pkgs else {}
            starter_str = (
                f"{starter.get('duration')}: ₹{starter.get('offer_price', starter.get('base_price')):,} "
                f"(Regular: ₹{starter.get('base_price', 0):,})" if starter else "N/A"
            )
            all_pkgs = ", ".join(f"{p.get('duration')}: ₹{p.get('offer_price', p.get('base_price', 0)):,}" for p in pkgs)
            trial_info = course_data.get("trial_classes", {})
            course_overview = course_data.get("course_overview", {})
            max_trials = (
                course_overview.get("max_trial_classes")
                or trial_info.get("max_trial_classes")
                or 3
            )
            demo_videos = trial_info.get("demo_videos") or course_data.get("demo_videos", {})
            demo_str = ", ".join(f"{teacher}: {url}" for teacher, url in demo_videos.items() if url) if demo_videos else "None"
            not_offered = course_data.get("not_offered", {})
            course_context_str = (
                f"ACTIVE COURSE CONTEXT (SOURCE OF TRUTH):\n"
                f"- Course Name: {course_name}\n"
                f"- Starter Package: {starter_str}\n"
                f"- All Package Offer Prices: {all_pkgs}\n"
                f"- Free Trial Available: Up to {max_trials} free trial class(es)\n"
                f"- YouTube Demo Videos: {demo_str}\n"
                f"- Unoffered / Excluded: {', '.join(not_offered.get('course_types', []))}"
            )

    # Quick pre-filter: if text is empty, ignore
    if not text or not text.strip():
        return {
            "is_refusal": False,
            "has_reason": False,
            "extracted_reason": None,
            "has_risk_or_legal": False,
            "risk_category": "NONE",
            "solution_viable": True,
            "resolution_type": "NOT_A_REFUSAL",
            "reply_message": None,
            "escalation_reason": None,
        }

    # Context history
    recent_context = []
    if history:
        for turn in history[-4:]:
            role = "Customer" if turn.get("role") == "user" else "Assistant"
            content = (turn.get("content") or "")[:200]
            recent_context.append(f"{role}: {content}")
    history_snippet = "\n".join(recent_context) if recent_context else "No prior history."

    refusal_stage = state.get("refusal_stage", 0)
    user_payload = (
        f"{course_context_str}\n"
        f"Current Refusal Stage: {refusal_stage} (0=new refusal, 1=we asked why — customer is now answering, 2=solution offered, 3=escalated)\n"
        f"Customer Message: \"{text}\"\n\n"
        f"Recent History:\n{history_snippet}\n\n"
        "Evaluate and output JSON:"
    )

    try:
        llm = _get_refusal_llm()
        resp = await llm.ainvoke([
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=user_payload),
        ])
        # BUG4 FIX: Robust JSON extraction that handles all code-fence variants.
        raw = resp.content.strip()
        if raw.startswith("```"):
            raw = raw.lstrip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        if raw.endswith("```"):
            raw = raw[:-3].strip()
        data = json.loads(raw)
        return data
    except Exception as e:
        print(f"[refusal_engine] Evaluation error: {e}")
        return {
            "is_refusal": False,
            "has_reason": False,
            "extracted_reason": None,
            "has_risk_or_legal": False,
            "risk_category": "NONE",
            "solution_viable": True,
            "resolution_type": "NOT_A_REFUSAL",
            "reply_message": None,
            "escalation_reason": None,
        }
