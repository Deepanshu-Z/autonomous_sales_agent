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


_SYSTEM_PROMPT = """You are an expert, empathetic course counsellor for Sensationz performing arts & wellness academy.
Your task is to generate a SHORT (1-2 sentences) follow-up message (Message 2) to be sent right after the bot has answered the customer's query (Message 1).

OBJECTIVE:
Engage the customer by sparking curiosity and asking an open, friendly, low-pressure question related to the topic just discussed.

CORE GUIDELINES:
1. STRICT LANGUAGE & SCRIPT MATCHING:
   - If the customer wrote in English -> Reply ONLY in pure English (no Hindi/Hinglish).
   - If the customer wrote in Hinglish (Latin alphabet) -> Reply in natural conversational Hinglish using ONLY the Latin alphabet (NEVER use Devanagari script).
   - If the customer wrote in Devanagari Hindi -> Reply in Devanagari Hindi. Use Devanagari ONLY if user typed in Devanagari.
2. CURIOSITY & ENGAGEMENT:
   - Connect naturally to what was discussed (e.g. their wellness goals, daily routine, beginner experience, schedule flexibility, past practice).
   - Avoid generic or aggressive sales pitches (do NOT say "Buy now", "Enroll today", or "Do you want to purchase?").
   - Make it easy and inviting for the customer to respond.
3. CONCISENESS:
   - Maximum 1-2 lines. Keep it crisp for WhatsApp.
   - Include 1 tasteful emoji (😊, ✨, 🌅, 🧘‍♀️).
4. SUPPRESSION RULES:
   - If the AI reply already ended with a specific question or call-to-action, return "NONE".
   - If the customer was asking for agent/human support or expressing refusal/complaint, return "NONE".
   - If there is nothing meaningful to follow up on, return "NONE".

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
        f"Recent History:\n{history_snippet}\n\n"
        "Generate the single curiosity-driven follow-up message (Message 2) or 'NONE':"
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
