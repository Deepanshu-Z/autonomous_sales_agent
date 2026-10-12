"""
meta_loader.py — Dynamic loader and helper for Meta campaigns & settings.
Zero hardcoding: all endpoints, post IDs, delay jitter, and templates are loaded from data/meta_campaigns.json.
"""

import os
import json
import random
import re
import asyncio
from typing import Dict, Any, List, Optional

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_CONFIG_FILE = os.path.join(_BASE_DIR, "data", "meta_campaigns.json")

_META_CONFIG_CACHE: Optional[Dict[str, Any]] = None


def clear_cache() -> None:
    """Clears the cached configuration."""
    global _META_CONFIG_CACHE
    _META_CONFIG_CACHE = None


def get_meta_config() -> Dict[str, Any]:
    """Loads and caches configuration from data/meta_campaigns.json."""
    global _META_CONFIG_CACHE
    if _META_CONFIG_CACHE is not None:
        return _META_CONFIG_CACHE

    if os.path.exists(_CONFIG_FILE):
        try:
            with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
                _META_CONFIG_CACHE = json.load(f)
                return _META_CONFIG_CACHE
        except Exception as e:
            print(f"[meta_loader] Error reading {_CONFIG_FILE}: {e}")

    _META_CONFIG_CACHE = {"settings": {}, "posts": []}
    return _META_CONFIG_CACHE


def get_meta_settings() -> Dict[str, Any]:
    """Retrieves settings section with env variable overrides."""
    cfg = get_meta_config()
    settings = cfg.get("settings", {})
    return {
        "graph_api_base": os.getenv("META_GRAPH_API_BASE", settings.get("graph_api_base", "https://graph.facebook.com")),
        "graph_api_version": os.getenv("META_GRAPH_API_VERSION", settings.get("graph_api_version", "v21.0")),
        "delay_min": float(os.getenv("META_REPLY_DELAY_MIN", settings.get("comment_reply_delay_min_seconds", 3))),
        "delay_max": float(os.getenv("META_REPLY_DELAY_MAX", settings.get("comment_reply_delay_max_seconds", 12))),
        "default_channel": settings.get("default_channel", "instagram"),
        "use_ai_comment_replies": bool(settings.get("use_ai_comment_replies", True)),
        "use_ai_private_dms": bool(settings.get("use_ai_private_dms", True)),
        "allow_all_dms": bool(settings.get("allow_all_dms", True)),
    }


def get_post_config(post_id: str) -> Optional[Dict[str, Any]]:
    """Finds active configuration for a given post/media ID."""
    if not post_id:
        return None
    clean_id = str(post_id).strip()
    cfg = get_meta_config()
    for post in cfg.get("posts", []):
        if not post.get("is_active", True):
            continue
        reg_id = str(post.get("post_id", "")).strip()
        if (
            reg_id in ("*", "ALL", "ANY")
            or clean_id == reg_id
            or clean_id.endswith(f"_{reg_id}")
            or reg_id.endswith(f"_{clean_id}")
        ):
            return post
    return None


def get_random_public_reply(post_cfg: Dict[str, Any], username: str = "") -> str:
    """Selects a randomized public comment reply and injects the username."""
    replies = post_cfg.get("public_replies", [])
    if not replies:
        return f"Hey @{username}! Check your DMs for details 📩" if username else "Check your DMs for details 📩"
    template = random.choice(replies)
    return template.replace("{username}", username or "there")


def get_random_delay() -> float:
    """Returns a randomized delay in seconds within configured range to avoid spam blocks."""
    s = get_meta_settings()
    min_d = max(0.5, s.get("delay_min", 3.0))
    max_d = max(min_d, s.get("delay_max", 12.0))
    return random.uniform(min_d, max_d)


def format_for_meta(text: str, max_length: int = 1000) -> str:
    """
    Cleans up text for Instagram / Facebook DMs:
    - Strips markdown asterisks (*bold* -> bold)
    - Converts markdown links [title](url) to title: url
    - Formats headers
    - Truncates to max allowed length safely
    """
    if not text:
        return ""
    # Headers
    cleaned = re.sub(r"^(?:#{1,6})\s+(.+)$", r"\1", text, flags=re.MULTILINE)
    # Convert markdown links [title](url) to title: url
    cleaned = re.sub(r"\[([^\]]+)\]\((https?://[^\)]+)\)", r"\1: \2", cleaned)
    # Loose bullet asterisks/hyphens to clean bullet points
    cleaned = re.sub(r"^\s*[\*\-]\s+", "• ", cleaned, flags=re.MULTILINE)
    # Remove all formatting asterisks (*bold* -> bold)
    cleaned = cleaned.replace("*", "")
    cleaned = cleaned.strip()
    if len(cleaned) > max_length:
        cleaned = cleaned[: max_length - 3] + "..."
    return cleaned


def _get_course_summary_str(course_id: str) -> str:
    """Extracts concise course facts from KB for comment & DM answering."""
    if not course_id:
        return ""
    try:
        from src.core import kb_loader
        data = kb_loader.get_course_data(course_id)
        overview = data.get("course_overview", {})
        pricing = data.get("pricing", {})
        name = overview.get("name") or course_id
        mode = overview.get("mode", "Online Live")
        days = overview.get("class_days", "Monday to Friday")
        pkgs = [
            f"{p.get('duration')}: ₹{p.get('offer_price', p.get('base_price'))}"
            for p in pricing.get("packages", [])
        ]
        pkg_str = ", ".join(pkgs[:3]) if pkgs else ""
        batches = data.get("batches", {})
        timing_list = []
        for slot in ["morning", "evening"]:
            for b in batches.get(slot, []):
                if b.get("time"):
                    timing_list.append(b.get("time"))
        timings_str = ", ".join(timing_list[:3]) if timing_list else "Morning & Evening slots available"
        return f"Course: {name} | Mode: {mode} ({days}) | Timings: {timings_str} | Fees: {pkg_str}"
    except Exception as e:
        print(f"[meta_loader] Error reading course summary for {course_id}: {e}")
        return ""


def _parse_json_from_llm(content: str) -> Dict[str, Any]:
    """Robustly parse JSON from LLM output, handling markdown blocks or extra surrounding text."""
    if not content:
        return {}
    s = content.strip()
    if s.startswith("```json"):
        s = s[7:]
    elif s.startswith("```"):
        s = s[3:]
    if s.endswith("```"):
        s = s[:-3]
    s = s.strip()
    try:
        return json.loads(s)
    except Exception:
        match = re.search(r"\{.*\}", s, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise


async def generate_crisp_comment_reply_async(comment_text: str, username: str, post_cfg: Dict[str, Any]) -> str:
    """
    Generates an ultra-crisp 1-sentence direct answer to the user's specific comment + DM notification.
    Falls back safely to random template if LLM is disabled or fails.
    """
    fallback = get_random_public_reply(post_cfg, username)
    settings = get_meta_settings()
    if not settings.get("use_ai_comment_replies", True):
        return fallback

    course_id = post_cfg.get("course", "")
    course_facts = _get_course_summary_str(course_id)
    user_handle = f"@{username}" if username else "there"

    system_prompt = (
        "You are the official social media manager for Sensationz Performing Arts.\n"
        "Generate a single, ultra-crisp public Instagram/Facebook comment reply (strictly under 18 words).\n"
        "Rules:\n"
        "1. Start with the user handle provided.\n"
        "2. Directly answer their specific question (e.g. fees, timings, online format) using the Course Info.\n"
        "3. If they just commented 'interested', 'info', or an emoji with no specific question, warmly acknowledge.\n"
        "4. Always end by stating you sent the full details/schedule to their DM with an emoji (e.g. 'Sent full details in your DM! 📩').\n"
        "5. Exactly 1 sentence. No markdown asterisks. Natural English or Hinglish matching the user.\n"
        "6. Return JSON format: {\"reply\": \"<your response>\"}"
    )

    user_prompt = (
        f"Course Info: {course_facts}\n"
        f"User ({user_handle}) comment: \"{comment_text}\""
    )

    try:
        from src.core.conversation_handler import _get_reply_llm
        from langchain_core.messages import SystemMessage, HumanMessage

        llm = _get_reply_llm()
        res = await asyncio.wait_for(
            llm.ainvoke([SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]),
            timeout=12.0,
        )
        data = _parse_json_from_llm(res.content)
        reply = (data.get("reply") or "").strip()
        if reply:
            return format_for_meta(reply)
    except Exception as e:
        print(f"[meta_loader] AI comment reply fallback due to: {repr(e)}")

    return fallback


async def generate_ai_private_dm_async(comment_text: str, username: str, post_cfg: Dict[str, Any]) -> str:
    """
    Generates an engaging, AI-crafted private DM answering the user's specific comment query,
    sharing relevant course details, and opening the conversation to guide them into the sales flow.
    Falls back safely to post_cfg["private_dm_intro"] if LLM is disabled or fails.
    """
    fallback = post_cfg.get("private_dm_intro", "")
    settings = get_meta_settings()
    if not settings.get("use_ai_private_dms", True):
        return fallback

    course_id = post_cfg.get("course", "")
    course_facts = _get_course_summary_str(course_id)
    user_handle = f"@{username}" if username else "there"

    system_prompt = (
        "You are the friendly, helpful admissions guide for Sensationz Performing Arts.\n"
        "A user commented on our social media post, and you are sending them a personalized private DM to answer their inquiry and start the conversation.\n"
        "Rules:\n"
        "1. Greet the user warmly (e.g., 'Hi!' or 'Hey there!').\n"
        "2. Directly answer their comment inquiry using the Course Info provided.\n"
        "3. Provide the key highlights concisely: Live interactive classes, schedule/days, starting fee overview.\n"
        "4. End with a friendly, low-friction question to continue the conversation (e.g. 'Would morning or evening batch suit you better?', 'Would you like to book a free trial class?').\n"
        "5. Keep the tone warm, welcoming, and concise (under 75 words).\n"
        "6. Do NOT use markdown asterisks (*bold*). Use clean text, bullet points (•), and tasteful emojis.\n"
        "7. Natural English or Hinglish matching the user's comment.\n"
        "8. Return JSON format: {\"private_dm\": \"<your response>\"}"
    )

    user_prompt = (
        f"Course Info: {course_facts}\n"
        f"User ({user_handle}) commented: \"{comment_text}\""
    )

    try:
        from src.core.conversation_handler import _get_reply_llm
        from langchain_core.messages import SystemMessage, HumanMessage

        llm = _get_reply_llm()
        res = await asyncio.wait_for(
            llm.ainvoke([SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]),
            timeout=12.0,
        )
        data = _parse_json_from_llm(res.content)
        dm_text = (data.get("private_dm") or "").strip()
        if dm_text:
            return format_for_meta(dm_text)
    except Exception as e:
        print(f"[meta_loader] AI private DM fallback due to: {repr(e)}")

    return fallback


def is_course_intent_dm(text: str) -> bool:
    """
    Evaluates whether an incoming organic direct message shows intent regarding courses,
    classes, pricing, timings, trial, or admission, so spam/random noise is filtered out.
    """
    if not text:
        return False
    t = text.lower().strip()

    # 1. Course names detected via KB
    try:
        from src.core import kb_loader
        if kb_loader.detect_course_from_text(t) is not None:
            return True
    except Exception:
        pass

    # 2. Key course inquiry / admission signals
    INTENT_KEYWORDS = [
        "fee", "fees", "price", "cost", "charge", "kitna", "kitne", "rate",
        "timing", "timings", "time", "slot", "batch", "kab", "morning", "evening",
        "class", "classes", "course", "courses", "session", "workshop",
        "trial", "demo", "free trial", "join", "enroll", "admission", "register",
        "online", "offline", "live", "start", "duration", "month", "months",
        "interested", "details", "info", "information", "help", "learn", "seekhna",
        "teach", "trainer", "teacher", "sir", "mam", "maam", "discount", "coupon",
        "yoga", "face yoga", "dance", "kathak", "acting", "music", "guitar", "singing"
    ]
    words = re.findall(r"\w+", t)
    return any(k in t for k in INTENT_KEYWORDS) or any(w in INTENT_KEYWORDS for w in words)


async def evaluate_and_generate_comment_actions_async(
    comment_text: str,
    username: str,
    post_cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Unified AI Intent & Safety Gate + Response Generator for Social Media Comments.
    In a single LLM call:
    1. Evaluates comment intent: INQUIRY/INTEREST vs COMPLAINT/TROLL/ABUSE/SPAM.
    2. If UNSAFE (complaint, troll, abuse, spam, pure friend tag with no inquiry):
       returns {"should_reply": False, "intent": "...", "reason": "..."}
    3. If SAFE (genuine inquiry, interest, positive emoji):
       returns {"should_reply": True, "intent": "...", "public_reply": "...", "private_dm": "..."}
    """
    # Quick Heuristic Safety Check first:
    # If comment is pure friend tagging (@name) without any text, do not reply
    clean_text = (comment_text or "").strip()
    words = clean_text.split()
    if words and all(w.startswith("@") for w in words):
        return {
            "should_reply": False,
            "intent": "SPAM_OR_IRRELEVANT",
            "reason": "Pure friend tag without comment text or inquiry",
            "public_reply": "",
            "private_dm": "",
        }

    course_id = post_cfg.get("course", "")
    course_facts = _get_course_summary_str(course_id)
    user_handle = f"@{username}" if username else "there"

    system_prompt = (
        "You are the lead social media strategist and admissions manager for Sensationz Performing Arts.\n"
        "Your task is to classify an incoming Instagram/Facebook comment for safety and intent, and if safe, generate the public reply and private DM.\n\n"
        "STEP 1: INTENT & SAFETY CLASSIFICATION\n"
        "Categorize the comment into exactly one of these categories:\n"
        "- 'INQUIRY': Asking a specific question about the course, fees, timings, trial, live format, trainers, etc.\n"
        "- 'INTEREST': Expressing genuine interest, joining enthusiasm, or positive supportive feedback (e.g. 'interested', 'details please', 'I want to join', 'loved this ❤️', 'superb 🔥').\n"
        "- 'COMPLAINT': Grievance, refund demand, service dissatisfaction, anger, dispute, scam/fraud accusations.\n"
        "- 'TROLL_OR_ABUSIVE': Insults, vulgarity, mockery, sarcasm, derogatory comments, offensive language, or harassing remarks.\n"
        "- 'SPAM_OR_IRRELEVANT': Self-promotion, crypto/ad links, completely unrelated banter, or ONLY tagging another user with no question.\n\n"
        "DECISION RULE FOR should_reply:\n"
        "- If intent is 'INQUIRY' or 'INTEREST': set 'should_reply': true.\n"
        "- If intent is 'COMPLAINT', 'TROLL_OR_ABUSIVE', or 'SPAM_OR_IRRELEVANT': set 'should_reply': false.\n\n"
        "STEP 2: IF should_reply IS TRUE, GENERATE RESPONSES:\n"
        "1. public_reply:\n"
        "   - Ultra-crisp, strictly under 18 words, exactly 1 sentence.\n"
        "   - Start with the user handle provided.\n"
        "   - Directly answer their question using the Course Info.\n"
        "   - Always end with notice that details/schedule were sent to their DM (e.g. 'Sent full details in your DM! 📩').\n"
        "   - Natural English/Hinglish matching the user. NO markdown asterisks (*bold*).\n"
        "2. private_dm:\n"
        "   - Warm, friendly, welcoming admissions guide message (under 75 words).\n"
        "   - Greet user warmly.\n"
        "   - Directly address their query using Course Info (fees, live online format Mon-Fri, morning/evening batches).\n"
        "   - End with a low-friction question to continue the conversation (e.g. 'Would morning or evening batch suit you better?').\n"
        "   - Tasteful emojis, clean formatting (bullet points •), NO markdown asterisks (*bold*).\n\n"
        "OUTPUT FORMAT: Return strictly valid JSON:\n"
        "{\n"
        "  \"should_reply\": true,\n"
        "  \"intent\": \"INQUIRY|INTEREST|COMPLAINT|TROLL_OR_ABUSIVE|SPAM_OR_IRRELEVANT\",\n"
        "  \"reason\": \"<brief justification>\",\n"
        "  \"public_reply\": \"<public comment reply if should_reply is true, else empty string>\",\n"
        "  \"private_dm\": \"<private dm if should_reply is true, else empty string>\"\n"
        "}"
    )

    user_prompt = (
        f"Course Info: {course_facts}\n"
        f"User ({user_handle}) comment: \"{clean_text}\""
    )

    try:
        from src.core.conversation_handler import _get_reply_llm
        from langchain_core.messages import SystemMessage, HumanMessage

        llm = _get_reply_llm()
        res = await asyncio.wait_for(
            llm.ainvoke([SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]),
            timeout=12.0,
        )
        data = _parse_json_from_llm(res.content)
        should_reply = bool(data.get("should_reply", True))
        intent = str(data.get("intent", "INQUIRY")).upper()
        reason = str(data.get("reason", ""))
        public_reply = format_for_meta(data.get("public_reply", "")) if should_reply else ""
        private_dm = format_for_meta(data.get("private_dm", "")) if should_reply else ""

        return {
            "should_reply": should_reply,
            "intent": intent,
            "reason": reason,
            "public_reply": public_reply,
            "private_dm": private_dm,
        }
    except Exception as e:
        print(f"[meta_loader] AI Intent evaluator fallback due to: {repr(e)}")

    # Fallback to existing logic if LLM call failed:
    from src.workers.tasks import is_complaint_or_refund
    if is_complaint_or_refund(clean_text):
        return {
            "should_reply": False,
            "intent": "COMPLAINT",
            "reason": "Regex fallback detected complaint/refund",
            "public_reply": "",
            "private_dm": "",
        }

    # Safe fallback
    return {
        "should_reply": True,
        "intent": "INQUIRY",
        "reason": "Heuristic fallback safe pass",
        "public_reply": get_random_public_reply(post_cfg, username),
        "private_dm": post_cfg.get("private_dm_intro", ""),
    }

