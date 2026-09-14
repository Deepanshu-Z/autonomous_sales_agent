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
    global _refusal_llm
    if _refusal_llm is None:
        _refusal_llm = ChatOpenAI(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            api_key=os.getenv("OPENAI_API_KEY"),
            temperature=0.2,
            max_tokens=400,
            timeout=10,
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
   - Note: If `awaiting_refusal_reason` is true, the customer is now responding with their reason for earlier refusal.

2. REASON PRESENCE:
   - Did the customer specify WHY they are refusing?
   - If they only said "not interested", "nahi chahiye", "no" -> `has_reason` = false.
   - If they explained (e.g., "fees are too high", "timing conflict", "busy schedule", "want offline classes", "scam company", "had surgery") -> `has_reason` = true.

3. RISK & LEGAL EVALUATION:
   - Does the message involve:
     • Legal threats, court, police, lawyer, consumer complaint, fraud/scam allegations, or chargeback threats?
     • Financial/payment disputes or unauthorized debit accusations?
     • Harassment, staff misconduct, or abusive incident?
     • Acute/severe medical hazards where taking exercise classes is unsafe?
   - If YES -> `has_risk_or_legal` = true.

4. SOLUTION VIABILITY:
   - Can our system/knowledge base legitimately solve the customer's reason?
     • Solvable: Budget/price (free trial, discount coupon, 1-month starter pack), schedule/timing (10 fixed batch slots 5AM–8PM to choose from, batch switch allowed after 1 month, app recordings), beginner fear (interactive beginner classes, certified guidance), trial/trust (free live trial, demo videos).
     • NOT Solvable: Customer insists on physical offline in-person studio classes, demands 1-on-1 private home tutors, demands cash payment at physical office, or has unserviceable demands.
   - If the request cannot be fulfilled by our offerings -> `solution_viable` = false.

DECISION OUTCOMES:
- If NOT a refusal (and not awaiting reason) -> `resolution_type` = "NOT_A_REFUSAL", `reply_message` = null.

1. HIGH RISK / LEGAL / HARASSMENT / ANGER (IMMEDIATE AGENT ESCALATION):
   - If the customer expresses legal threats, financial/chargeback disputes, harassment, scam allegations, or direct anger/accusations (e.g. "u r lier", "jhuth bola", "scam company", "police complaint", "fraud", "consumer court"):
     -> `resolution_type` = "ESCALATE_TO_AGENT"
     -> `reply_message`: Sincerely apologize for the frustration/miscommunication, reassure them that we take their concern seriously, and let them know our senior human support specialist is stepping in right away to assist them directly.
     -> `escalation_reason`: English summary of the risk/complaint for the human agent.

2. PERSISTENT REFUSAL OR REPEATED OBJECTION (AFTER SOLUTION ALREADY GIVEN OR REFUSAL STAGE >= 2):
   - If a solution/starter package/trial was ALREADY offered in conversation (or `Refusal Stage` is >= 2), AND the customer still refuses, continues objecting, expresses dissatisfaction, or declines:
     -> CRITICAL ANTI-REPETITION RULE: You MUST NEVER repeat, re-pitch, or re-explain the starter package, fees, or trial classes again! Doing so sounds like an unaware, annoying robot!
     -> `resolution_type` = "ESCALATE_TO_AGENT"
     -> `reply_message`: Warmly and gracefully accept their decision and respect their choice without being pushy or arguing (e.g. "Hum samajhte hain aur aapki choice ka poora sammaan karte hain! 😊 Agar aapko future mein kabhi bhi koi guidance ya classes ki zaroorat ho, toh hamara support team aapse connect karne ke liye hamesha available hai. Have a wonderful day!").
     -> `escalation_reason`: "Customer declined after solution was offered (persistent refusal)".

3. FIRST-TIME OBJECTION: BUDGET / FEES / COMPETITOR PRICING (REFUSAL STAGE == 1):
   - If the customer mentions fees are too high, or compares with competitors ("Fees jyda h", "ABC classes sasti h", "XYZ gives 30 days trial / Rs 100"):
     • If a solution/starter package was ALREADY offered in recent history: Treat as PERSISTENT REFUSAL (Rule 2) -> `ESCALATE_TO_AGENT`.
     • If this is the FIRST time they raise fees/budget/competitor:
       -> `resolution_type` = "PROVIDE_SOLUTION"
       -> `solution_viable` = true
       -> `reply_message`: Warmly acknowledge their concern. Reframe on the value difference (100% Live Interactive with certified instructors observing on camera with real-time posture correction, unlike pre-recorded videos or automated mass apps). Offer the 1-Month Starter Package and up to 3 free live trial classes from ACTIVE COURSE CONTEXT so they can experience it with zero risk.

4. FIRST-TIME REFUSAL WITH OTHER REASONS (REFUSAL STAGE == 1):
   - If the customer specifies why they are hesitating/refusing:
     • SOLVABLE REASONS -> `resolution_type` = "PROVIDE_SOLUTION", `solution_viable` = true:
       1. Doubt about teachers, exercise pace, or beginner guidance: Share YouTube demo video links or offer free live trial classes so they can see the beginner pace firsthand.
       2. General hesitation or wanting to try first: Offer free live trial classes.
     • UNRESOLVABLE REASONS -> `resolution_type` = "ESCALATE_TO_AGENT", `solution_viable` = false:
       1. Demands flexible / shifting batch timings or on-demand hours: Unresolvable! We only offer 10 fixed 1-hour slots (5:00 AM to 8:00 PM). Politely explain our fixed-batch schedule, and let them know our human coordinator will assist them.
       2. Demands offline classes / physical studio visits / 1-on-1 private home tutors / cash payments: Unresolvable! Politely explain our 100% online live format, and let them know our coordinator will assist them.
       3. Severe medical hazard / legal threats / dispute: Unresolvable! Escalate immediately.

5. FIRST-TIME REFUSAL WITHOUT REASON (REFUSAL STAGE == 0):
   - If the customer refuses/declines without providing any explanation (e.g. "not interested", "leave it", "nahi chahiye", "no thanks"):
     -> `resolution_type` = "ASK_REASON"
     -> `reply_message`: Warmly and empathetically ask what is holding them back (timing, fees, hesitation, or something else) so we can see if we can help. Never pressure.

CRITICAL ANTI-LOOP RULE (NON-NEGOTIABLE):
- If `Awaiting Refusal Reason from previous turn` is True OR `Current Refusal Stage` >= 1:
  • You are STRICTLY FORBIDDEN from choosing `ASK_REASON`!
  • You MUST NEVER ask "kya aap humein bata sakte hain...", "what is holding you back...", or ask why they are refusing a second time!
  • Customer messages on this turn (including shorthand, typos, or slang like "flexible tm", "tm", "fees", "paise", "cost", "timing", "time nahi h", "bkr h") ARE THEIR STATED REASON -> set `has_reason: true`!
  • Slang decoding: "tm" / "tym" = timing, "bkr" = bekaar/bad, "jyda" = high, "krna" = karna.
  • If their stated reason is flexible timings: Sensationz does NOT offer flexible timings (only fixed 1-hour batch slots 5AM-8PM). Therefore, this is UNRESOLVABLE (`solution_viable: false`) -> `resolution_type: "ESCALATE_TO_AGENT"`!
  • If the customer simply repeats refusal without reason (e.g. "nahi chahiye", "rehne do", "no", "nahi lena") -> Do NOT ask again! Respectfully accept and escalate to agent (`ESCALATE_TO_AGENT`).

6. GENERAL / META / IDENTITY QUESTIONS (NOT A REFUSAL):
   - If the customer asks questions like "are you an AI?", "kya tum bot ho?", "who are you?", "kaun ho?", or questions about class schedules/curriculum:
     -> `resolution_type` = "NOT_A_REFUSAL"
     -> `reply_message` = null

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
  "reply_message": string or null (MUST NOT be null when resolution_type is ASK_REASON, PROVIDE_SOLUTION, or ESCALATE_TO_AGENT),
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
    awaiting_reason = bool(state.get("awaiting_refusal_reason", False))
    
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
            starter = pkgs[0] if pkgs else {}
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
            unoffered_items = list(not_offered.get("course_types", [])) + list(not_offered.get("other", []))
            unoffered_items.append("Flexible/on-demand timings (only 10 fixed batches 5AM-8PM available)")
            course_context_str = (
                f"ACTIVE COURSE CONTEXT (SOURCE OF TRUTH):\n"
                f"- Course Name: {course_name}\n"
                f"- Starter Package: {starter_str}\n"
                f"- All Package Offer Prices: {all_pkgs}\n"
                f"- Free Trial Available: Up to {max_trials} free trial class(es)\n"
                f"- YouTube Demo Videos: {demo_str}\n"
                f"- Unoffered / Excluded: {', '.join(unoffered_items)}"
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

    # History-aware offer tracking: identify what assistant already pitched in recent turns
    prior_assistant_replies = " ".join((t.get("content") or "") for t in (history or []) if t.get("role") in ("assistant", "ai")).lower()
    starter_pack_already_offered = bool(
        "starter package" in prior_assistant_replies
        or "starter pack" in prior_assistant_replies
        or "210" in prior_assistant_replies
        or "₹210" in prior_assistant_replies
    )
    trial_already_offered = bool(
        "free trial" in prior_assistant_replies
        or "trial classes" in prior_assistant_replies
        or "trial class" in prior_assistant_replies
    )

    # Infer refusal stage directly from history (Bulletproof against lost state)
    inferred_stage = state.get("refusal_stage", 0)
    if history:
        for turn in reversed(history[-4:]):
            if turn.get("role") in ("assistant", "ai"):
                c = (turn.get("content") or "").lower()
                if any(q in c for q in ["kya rok raha hai", "kya timing hai, fees", "kyun interested nahi", "what is holding you back"]):
                    inferred_stage = max(inferred_stage, 1)
                elif any(q in c for q in ["starter package", "starter pack", "210", "free trial", "trial classes"]):
                    inferred_stage = max(inferred_stage, 2)
                break
    refusal_stage = inferred_stage
    awaiting_reason = awaiting_reason or (refusal_stage == 1)

    user_payload = (
        f"{course_context_str}\n"
        f"Current Refusal Stage: {refusal_stage} (0=new refusal, 1=asked reason, 2=solution offered)\n"
        f"Awaiting Refusal Reason from previous turn: {awaiting_reason or refusal_stage == 1}\n"
        f"Already Offered in Recent History:\n"
        f"- Starter Package already pitched: {starter_pack_already_offered}\n"
        f"- Trial classes already pitched: {trial_already_offered}\n"
        f"- Non-Negotiable Rule: If an offer was already pitched, NEVER pitch it again!\n\n"
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
        raw = resp.content.strip().strip("`").replace("json\n", "").strip()
        data = json.loads(raw)

        # Code-level anti-loop guard:
        # If refusal_stage >= 1 and LLM still returned ASK_REASON, prevent infinite repetition!
        if (refusal_stage >= 1 or awaiting_reason) and data.get("resolution_type") == "ASK_REASON":
            data["resolution_type"] = "ESCALATE_TO_AGENT"
            data["solution_viable"] = False
            data["escalation_reason"] = f"Customer replied to refusal inquiry: {text[:60]}"
            data["reply_message"] = (
                "Hum samajhte hain aur aapki choice ka poora sammaan karte hain! 😊 "
                "Agar aapko future mein kabhi bhi koi guidance ya classes ki zaroorat ho, toh hamara support team aapse connect karne ke liye hamesha available hai. Have a wonderful day!"
            )

        # Code-level anti-repetition guard:
        # If refusal_stage >= 2 and LLM tried to PROVIDE_SOLUTION again,
        # OR if customer is still pushing back after a solution was offered:
        if refusal_stage >= 2 and data.get("resolution_type") == "PROVIDE_SOLUTION":
            data["resolution_type"] = "ESCALATE_TO_AGENT"
            data["solution_viable"] = False
            data["escalation_reason"] = "Customer declined/countered after solution was already offered (persistent refusal)"
            data["reply_message"] = (
                "Hum samajhte hain aur aapki choice ka poora sammaan karte hain! 😊 "
                "Agar aapko future mein kabhi bhi koi guidance ya live classes ki zaroorat ho, toh hamara support team aapse connect karne ke liye hamesha available hai. Have a wonderful day!"
            )

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
