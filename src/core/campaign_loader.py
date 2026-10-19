"""
campaign_loader.py — Centralized Ad Campaign & Welcome Flow Mapping Loader.

Loads data/ad_campaigns.json to map:
- Meta Ad IDs & ad tags -> Course Name
- Initial welcome messages (Message 1 intro + Message 2 follow-up)
- 3 interactive quick reply buttons ("Timing", "Fees", "Other questions")
"""

import os
import json
from typing import Dict, Any, List, Optional

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_CAMPAIGNS_FILE = os.path.join(_BASE_DIR, "data", "ad_campaigns.json")

_CAMPAIGNS_CACHE: Optional[List[Dict[str, Any]]] = None


def clear_cache() -> None:
    """Clears campaigns cache (useful during testing or hot reloading)."""
    global _CAMPAIGNS_CACHE
    _CAMPAIGNS_CACHE = None


def get_all_campaigns() -> List[Dict[str, Any]]:
    """Loads and caches all campaigns from data/ad_campaigns.json."""
    global _CAMPAIGNS_CACHE
    if _CAMPAIGNS_CACHE is not None:
        return _CAMPAIGNS_CACHE

    if os.path.exists(_CAMPAIGNS_FILE):
        try:
            with open(_CAMPAIGNS_FILE, "r", encoding="utf-8") as f:
                _CAMPAIGNS_CACHE = json.load(f)
                return _CAMPAIGNS_CACHE
        except Exception as e:
            print(f"[campaign_loader] Error loading {_CAMPAIGNS_FILE}: {e}")

    _CAMPAIGNS_CACHE = []
    return _CAMPAIGNS_CACHE


def is_registered_ad_id(ad_id: str, source_url: str = "") -> bool:
    """Checks if an Ad ID or source URL matches any registered campaign."""
    clean_id = str(ad_id).strip() if ad_id else ""
    clean_url = str(source_url).strip() if source_url else ""
    if not clean_id and not clean_url:
        return False

    for camp in get_all_campaigns():
        for registered_id in camp.get("ad_ids", []):
            reg_str = str(registered_id).strip()
            if reg_str and (clean_id == reg_str or (clean_url and reg_str in clean_url)):
                return True
    return False


def match_campaign(referral: Optional[dict] = None, text: str = "") -> Optional[Dict[str, Any]]:
    """
    Strictly finds the campaign matching the exact Meta Ad ID from referral metadata.
    If referral contains an ad_id/source_id, it MUST be registered, otherwise returns None.
    If no ad_id is present (e.g. whitelisted direct WhatsApp testing), falls back to course detection or default campaign.
    """
    campaigns = get_all_campaigns()
    if not campaigns:
        return None

    # 1. Production Ad Flow: Strictly match exact Meta Ad ID
    if referral and isinstance(referral, dict):
        ad_id = str(referral.get("ad_id") or referral.get("source_id") or "").strip()
        source_url = str(referral.get("source_url") or "").strip()

        if ad_id or source_url:
            for camp in campaigns:
                for registered_id in camp.get("ad_ids", []):
                    reg_str = str(registered_id).strip()
                    if reg_str and (ad_id == reg_str or (source_url and reg_str in source_url)):
                        return camp

            from src.core.meta_loader import get_post_config
            post_cfg = get_post_config(ad_id)
            if post_cfg:
                for camp in campaigns:
                    if camp.get("course") == post_cfg.get("course"):
                        return camp

            return None  # Ad ID provided but not registered -> strictly ignore

    # 2. Direct Testing / Whitelisted Fallback (no ad referral attached)
    from src.core import kb_loader
    detected_course = kb_loader.detect_course_from_text(text) if text else None
    if detected_course:
        for camp in campaigns:
            if camp.get("course") == detected_course:
                return camp

    # Fall back to first campaign only when explicitly in TEST_MODE
    if os.getenv("TEST_MODE", "").strip().lower() in ("true", "1", "yes"):
        return campaigns[0]

    return None


def get_welcome_messages(campaign: Optional[Dict[str, Any]], language: Optional[str] = None) -> Dict[str, str]:
    """
    Extracts localized welcome messages (message_1, message_2) from campaign data.
    Supports both language-keyed dictionaries and flat legacy dictionaries.
    """
    if not campaign:
        return {}

    raw_msgs = campaign.get("welcome_messages", {})
    if not raw_msgs:
        return {}

    # If already a flat legacy dict with message_1
    if "message_1" in raw_msgs:
        return raw_msgs

    # Language-keyed dictionary
    lang_key = (language or "english").strip().lower()
    if lang_key in raw_msgs:
        return raw_msgs[lang_key]

    # Fallback to english, then first available language
    if "english" in raw_msgs:
        return raw_msgs["english"]

    first_val = next(iter(raw_msgs.values()), {})
    return first_val if isinstance(first_val, dict) else {}


def get_campaign_buttons(campaign: Optional[Dict[str, Any]], language: Optional[str] = None) -> List[str]:
    """
    Extracts localized quick-reply buttons from campaign data.
    Supports both language-keyed button dictionaries and flat button lists.
    """
    if not campaign:
        return ["Fees", "Timings", "Talk to Agent"]

    raw_buttons = campaign.get("buttons")
    if isinstance(raw_buttons, list):
        return raw_buttons

    if isinstance(raw_buttons, dict):
        lang_key = (language or "english").strip().lower()
        if lang_key in raw_buttons:
            return raw_buttons[lang_key]
        if "english" in raw_buttons:
            return raw_buttons["english"]
        first_val = next(iter(raw_buttons.values()), [])
        if isinstance(first_val, list):
            return first_val

    return ["Fees", "Timings", "Talk to Agent"]

