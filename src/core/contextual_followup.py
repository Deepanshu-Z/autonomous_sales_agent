"""
contextual_followup.py — Reusable Dynamic Contextual Engagement & Follow-up Engine.

Generates context-aware, curiosity-driven follow-up messages (Message 2)
after an informational reply (Message 1), without any hardcoded questions or keyword maps.
Strictly respects customer language (English, Hindi, Hinglish) and funnel context.
"""

import os
import time
import asyncio
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from src.core import kb_loader

load_dotenv()

# Singleton LLM instance for fast contextual follow-up generation
_followup_llm = None

def _get_followup_llm():
    global _followup_llm
    if _followup_llm is None:
        _followup_llm = ChatOpenAI(
            model=os.getenv("OPENAI_MODEL", "gpt-4o"),
            api_key=os.getenv("OPENAI_API_KEY"),
            temperature=0.4,
            timeout=12,
            max_retries=1,
        )
    return _followup_llm


_SYSTEM_PROMPT = """You are a world-class sales counsellor for Sensationz — a performing arts and wellness academy.
Your task is to generate a SHORT (1-2 sentences) follow-up message (Message 2) sent right after the bot's reply (Message 1).

OBJECTIVE:
Move the customer ONE step closer to action using proven conversion psychology — not generic questions.

CONVERSION PSYCHOLOGY TOOLKIT (pick the best fit for the context):
1. IDENTITY TRANSFORMATION: People buy who they will BECOME, not features. Ask about their desired outcome.
   Example: "Aap 1 month baad khud mein kya change dekhna chahte hain?" or "What's the one thing you're hoping this course will change for you? 😊"

2. SOCIAL PROOF (subtle): Reference real student experience to reduce risk perception.
   Example: "Btw, most students who hesitate at this step say it was the best decision they made 😊 Kya try karna chahenge?"

3. ENDOWMENT EFFECT: Remind them they already have something locked in — make them feel the loss of NOT completing.
   Example: "Aapka batch slot aur discount credit already aapke number pe hold hai — sirf 1 step bacha hai! 🎯"

4. MICRO-COMMITMENT: Ask a tiny, easy question that leads to a yes.
   Example: "Kya aapka phone Android hai ya iPhone? Main exact steps bhejta hoon 📲"

5. CURIOSITY GAP: Give a surprising fact then close with action.
   Example: "Did you know our 6 AM batch students report 2x better energy by Week 2? Kaunsa timing aapke liye best hoga? 😊"

CORE RULES:
- STRICT LANGUAGE & SCRIPT MATCHING: Match exact language and script of customer's message (English, Hindi, Hinglish, etc.).
- Use Latin/English alphabet if the user wrote in transliteration — NEVER switch to Devanagari unless customer used it.
- Max 1-2 lines. Keep it WhatsApp-crisp.
- Include 1 tasteful emoji (😊, ✨, 🌅, 🧘‍♀️, 🎯, 📲).
- NEVER say "Buy now", "Enroll today", or "Do you want to purchase?" — too pushy.
- If the AI reply already ended with a direct question or CTA, return "NONE".
- If the customer was asking for agent/human support or expressing complaint, return "NONE".
- If no meaningful follow-up fits, return "NONE".

Return ONLY the plain follow-up message text, or "NONE" if no follow-up should be sent."""


def should_suppress_followup(user_text: str, ai_reply: str, state: Dict[str, Any]) -> bool:
    """
    Evaluates dynamic suppression criteria based on state and message content.
    """
    if not user_text or not ai_reply:
        return True

    # 1. State-level suppressions
    if state.get("is_escalated"):
        return True

    terminal_stages = {"PROFILE_COMPLETED", "COUPON_SENT"}
    if state.get("stage") in terminal_stages:
        return True

    # 2. If the user explicitly requested a human agent
    agent_triggers = {"agent", "human", "support", "call me", "representative"}
    u_lower = user_text.lower().strip()
    if any(trigger in u_lower for trigger in agent_triggers):
        return True

    return False


async def generate_contextual_followup_async(
    user_text: str,
    ai_reply: str,
    history: Optional[List[Dict[str, str]]] = None,
    state: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """
    Asynchronously generates a dynamic, curiosity-driven follow-up message based on
    the conversation context, query, and AI reply. Zero hardcoded question strings.
    """
    state = state or {}
    if should_suppress_followup(user_text, ai_reply, state):
        return None

    course_id = (state.get("course") or "").strip()
    if not course_id and history:
        from src.core import kb_loader
        hist_text = " ".join((t.get("content") or "") for t in history[-6:])
        course_id = kb_loader.detect_course_from_text(hist_text, default_course=None) or ""
    course_name = course_id.title() if course_id else "General"

    # Fetch a random course conversion hook to inject as optional fact context
    conversion_hook_str = ""
    if course_id:
        try:
            from src.core import kb_loader as _kbl
            hook = _kbl.get_course_conversion_hook(course_id)
            if hook:
                conversion_hook_str = f"\nOptional Course Fact (use naturally if fits context — don't force it):\n{hook}"
        except Exception:
            pass

    # Summarize recent turns for context
    recent_context = []
    if history:
        for turn in history[-4:]:
            role = "Customer" if turn.get("role") == "user" else "Assistant"
            content = (turn.get("content") or "")[:200]
            recent_context.append(f"{role}: {content}")
    history_snippet = "\n".join(recent_context) if recent_context else "No prior history."

    user_payload = (
        f"Active Course: {course_name}\n"
        f"Customer's Message: \"{user_text}\"\n"
        f"AI's Direct Answer: \"{ai_reply}\"\n\n"
        f"Recent History:\n{history_snippet}"
        f"{conversion_hook_str}\n\n"
        "Generate the single conversion-psychology follow-up message (Message 2) or 'NONE':"
    )


    try:
        llm = _get_followup_llm()
        resp = await llm.ainvoke([
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=user_payload),
        ])
        content = resp.content.strip()

        # Clean quotes if model wrapped output
        if (content.startswith('"') and content.endswith('"')) or (content.startswith("'") and content.endswith("'")):
            content = content[1:-1].strip()

        if not content or content.upper() == "NONE" or len(content) < 5:
            return None

        return content
    except Exception as e:
        print(f"[contextual_followup] Generation failed: {e}")
        return None
