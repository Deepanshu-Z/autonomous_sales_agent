"""
intent_classifier.py — Hybrid Intent Classifier (Regex Fast-Path + LLM Fallback).

PURPOSE:
Permanently solves the problem of Hindi/Hinglish misspellings breaking coupon
and profile-completion detection. Instead of endlessly adding regex patterns,
this module uses a two-tier approach:

  Tier 1 (Fast Path): Existing regex detectors run first (zero cost, zero latency).
  Tier 2 (LLM Fallback): If regex misses but the message contains coupon-like or
    profile-like words (detected via fuzzy matching), a lightweight LLM call
    classifies the intent. This catches ANY misspelling or creative phrasing.

USAGE:
    from intent_classifier import classify_coupon_and_profile_intent
    result = await classify_coupon_and_profile_intent(text, state)
    # result = {"is_coupon_request": bool, "is_profile_done": bool, "is_coupon_info": bool}
"""

import re
import json
import os
from rapidfuzz import fuzz
from langchain_core.messages import SystemMessage, HumanMessage
from dotenv import load_dotenv

load_dotenv()

# ── Lazy LLM singleton (avoids circular imports with rag.py) ────────────────
_classifier_llm = None

def _get_classifier_llm():
    """Lazy-init a lightweight LLM for intent classification."""
    global _classifier_llm
    if _classifier_llm is None:
        from langchain_openai import ChatOpenAI
        _classifier_llm = ChatOpenAI(
            model="gpt-4.1-nano",
            api_key=os.getenv("OPENAI_API_KEY"),
            temperature=0,
            timeout=10,
            max_retries=1,
        )
    return _classifier_llm


# ── Fuzzy Pre-Filter ────────────────────────────────────────────────────────
# These words/stems trigger an LLM call when the regex misses.

_COUPON_CANONICAL = "coupon"
_COUPON_FUZZY_THRESHOLD = 60  # fuzz.ratio >= 60 → probably means "coupon"
_COUPON_REGEX = re.compile(r"\b[ckq][aeou]{1,3}p[aeou]{0,2}n[a-z]{0,4}\b", re.IGNORECASE)

# Short stems that indicate profile/completion context
_PROFILE_STEMS = [
    "profile", "account", "signup", "register", "login",
    "done", "ready", "complete", "created", "downloaded", "installed",
    "bna", "bana", "banali", "bnali", "banadi", "bnadi", "banaya", "banayi",
    "kardi", "krdi", "kardiya", "krdiya", "hogaya", "hogya", "hgya", "hgaya", "bangaya", "bangya",
    "krlia", "krliya", "karliya", "karlia", "karli", "krli", "kardia", "krdia",
    "nipat", "nipta", "khatam", "kaam", "dono", "finish", "setup",
    "hn", "han", "haa", "hnji", "hanji",
]

_COMPLETION_VERBS_HINDI = [
    "le hai", "le h", "le he",
    "liya hai", "liya h", "li hai", "li h",
    "chuka", "chuki", "gaya", "gya", "gayi", "gai",
    "diya", "di hai", "di h", "hgya", "h gya", "krdia na", "kr diya na",
]


def _has_fuzzy_coupon_word(text: str) -> bool:
    """Check if any word in text fuzzy-matches 'coupon' or follows phonetic coupon variations."""
    words = re.findall(r"\w+", text.lower())
    for w in words:
        if _COUPON_REGEX.search(w):
            return True
        if len(w) >= 4 and (fuzz.ratio(w, _COUPON_CANONICAL) >= _COUPON_FUZZY_THRESHOLD or fuzz.partial_ratio(_COUPON_CANONICAL, w) >= 70):
            return True
    return False


def _has_profile_like_content(text: str) -> bool:
    """Check if text contains profile/completion-related words."""
    t = text.lower()
    # Check stems
    if any(stem in t for stem in _PROFILE_STEMS):
        return True
    # Check Hindi completion verb phrases
    if any(verb in t for verb in _COMPLETION_VERBS_HINDI):
        return True
    return False


# ── Unified Post-Link Classification Prompt ──────────────────────────────

_UNIFIED_CLASSIFIER_SYSTEM_PROMPT = """You classify incoming customer WhatsApp messages for a Yoga Studio chatbot.
The customer may write in Hindi, Hinglish, or English with typos, abbreviations, or informal slang.

CURRENT FUNNEL CONTEXT:
The AI just sent app download & website links and instructed: "Download Sensationz App / visit website, create your profile, and reply Done or Yes to unlock your welcome discount coupon code."

TASK:
Classify the customer's message into EXACTLY ONE primary intent:
1. "COUPON_OR_PROFILE_DONE":
   - Customer states they completed the task/signup/profile/download ("hn hn hgya h", "krdia na", "done", "yes", "bana li", "ho gaya", "created", "sab kar diya").
   - OR customer asks to receive/view their coupon code ("coupon to bjho", "code do", "send coupon", "1y ka coupon bjhdo", "konsa coupon rhga", "bhai code do").
2. "QUESTION":
   - Customer asks what to do next ("ab kya krna h?", "aage kya karein?").
   - Customer asks an informational/troubleshooting question ("how to apply?", "iphone pe kaise hoga?", "classes kab hongi?", "GST kitna lagega?", "teacher kaun hai?").
3. "DISINTEREST":
   - Customer refuses or declines ("nahi chahiye", "not interested", "cancel").
4. "AGENT":
   - Customer asks for human support / phone call ("agent", "human", "call me").
5. "OTHER":
   - Casual greeting or uncategorized.

Also extract course package duration if mentioned ("1 Month", "3 Months", "6 Months", "1 Year", or null).

Return ONLY strict JSON:
{
    "intent": "COUPON_OR_PROFILE_DONE" | "QUESTION" | "DISINTEREST" | "AGENT" | "OTHER",
    "is_coupon_request": bool,
    "is_profile_done": bool,
    "package": string | null
}"""


async def _llm_classify_unified(text: str) -> dict:
    """Call the LLM for unified intent & package classification."""
    try:
        llm = _get_classifier_llm()
        resp = await llm.ainvoke([
            SystemMessage(content=_UNIFIED_CLASSIFIER_SYSTEM_PROMPT),
            HumanMessage(content=f'Customer Message: "{text}"'),
        ])
        raw = resp.content.strip().strip("`").replace("json\n", "").strip()
        data = json.loads(raw)
        return {
            "intent": str(data.get("intent", "OTHER")),
            "is_coupon_request": bool(data.get("is_coupon_request", False)),
            "is_profile_done": bool(data.get("is_profile_done", False)),
            "package": data.get("package") if data.get("package") in ["1 Month", "3 Months", "6 Months", "1 Year"] else None,
        }
    except Exception as e:
        print(f"[intent_classifier] LLM fallback error: {e}")
        return {"intent": "OTHER", "is_coupon_request": False, "is_profile_done": False, "package": None}


# ── Public APIs ──────────────────────────────────────────────────────────────

async def classify_coupon_and_profile_intent(
    text: str,
    state: dict,
    regex_coupon_result: bool,
    regex_profile_result: bool,
    regex_coupon_info_result: bool = False,
) -> dict:
    """
    Hybrid intent classifier for backward compatibility and fast paths.
    """
    # ── Fast Path: If regex already clearly resolved profile or coupon request ──
    if regex_profile_result or regex_coupon_result:
        return {
            "is_coupon_request": regex_coupon_result,
            "is_profile_done": regex_profile_result,
            "is_coupon_info": regex_coupon_info_result,
        }

    # ── LLM Unified Classifier ──
    llm_res = await _llm_classify_unified(text)
    is_cpn = regex_coupon_result or llm_res["is_coupon_request"] or (llm_res["intent"] == "COUPON_OR_PROFILE_DONE" and not llm_res["is_profile_done"])
    is_prof = regex_profile_result or llm_res["is_profile_done"] or (llm_res["intent"] == "COUPON_OR_PROFILE_DONE")
    return {
        "is_coupon_request": is_cpn,
        "is_profile_done": is_prof,
        "is_coupon_info": regex_coupon_info_result or (llm_res["intent"] == "QUESTION"),
        "package": llm_res.get("package"),
    }


async def classify_post_link_intent(
    text: str,
    state: dict,
    regex_coupon_result: bool = False,
    regex_profile_result: bool = False,
) -> dict:
    """
    Unified 2-path router after app links are sent:
    Routes directly to COUPON_OR_PROFILE_DONE, QUESTION, DISINTEREST, or AGENT.
    """
    # 1. Fast path: If regex confirms profile done or coupon ask
    if regex_profile_result or regex_coupon_result:
        return {
            "intent": "COUPON_OR_PROFILE_DONE",
            "is_coupon_request": regex_coupon_result,
            "is_profile_done": regex_profile_result,
            "package": None,
        }

    # 2. LLM Unified Classifier
    return await _llm_classify_unified(text)
