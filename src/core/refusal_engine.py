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
- Offerings: Flexible daily batch timings (5:00 AM to 8:00 PM), recorded sessions in app, starter packages, up to 3 free live trial classes, certified instructors, beginner-friendly pace.

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
     • Solvable: Budget/price (free trial, discount coupon, 1-month starter pack), schedule/timing (10 flexible batches, app recordings), beginner fear (interactive beginner classes, certified guidance), trial/trust (free live trial, demo videos).
     • NOT Solvable: Customer insists on physical offline in-person studio classes, demands 1-on-1 private home tutors, demands cash payment at physical office, or has unserviceable demands.
   - If the request cannot be fulfilled by our offerings -> `solution_viable` = false.

DECISION OUTCOMES:
- If NOT a refusal (and not awaiting reason) -> `resolution_type` = "NOT_A_REFUSAL".
- If REFUSAL and `has_reason` = false:
  -> `resolution_type` = "ASK_REASON"
  -> `reply_message`: Warmly and empathetically ask what is holding them back (timing, fees, or something else). Never pressure.
- If `has_reason` = true AND (`has_risk_or_legal` = true OR `solution_viable` = false):
  -> `resolution_type` = "ESCALATE_TO_AGENT"
  -> `reply_message`: Tell the customer clearly and politely that we have listened to their query/concern and we are assigning a human support specialist from our team to assist them directly.
  -> `escalation_reason`: Concise English summary of the risk or unresolvable concern for the human agent.
- If `has_reason` = true AND `has_risk_or_legal` = false AND `solution_viable` = true:
  -> `resolution_type` = "PROVIDE_SOLUTION"
  -> `reply_message`: Warm, empathetic message offering our knowledge-based solution (e.g. flexible batch, free trial, starter package) without being pushy. Keep it inviting.

STRICT LANGUAGE & SCRIPT MATCHING FOR `reply_message`:
- English input -> Pure English.
- Hinglish input (Latin alphabet) -> Natural Conversational Hinglish using ONLY the Latin alphabet (NEVER output Devanagari script).
- Devanagari Hindi input -> Devanagari Hindi. Use Devanagari ONLY if user typed in Devanagari.

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
    Returns structured decision dictionary. Zero hardcoded regex or static strings.
    """
    state = state or {}
    awaiting_reason = bool(state.get("awaiting_refusal_reason", False))
    course_id = state.get("course") or "yoga"

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

    user_payload = (
        f"Active Course: {course_id}\n"
        f"Awaiting Refusal Reason from previous turn: {awaiting_reason}\n"
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
