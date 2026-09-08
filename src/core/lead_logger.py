"""
lead_logger.py — Centralized console logging for incoming WhatsApp leads.

Outputs formatted console log:
------------------------------------------------------------------------------------------------------------
----------------------------------------------------------------------------------------------------------
LEAD NO: <number received>
LEAD DETAILS: Ad ID: <id> | Ad Tag: <tag>
ACTION TAKEN: <REPLIED or IGNORED>
REASON: <reason for decision>
------------------------------------------------------------------------------------------------------------
"""

import os
import re
import json


def extract_lead_details(
    referral: dict = None,
    customer: dict = None,
    message: dict = None,
    state: dict = None,
    phone: str = None,
) -> str:
    """
    Extracts ad id and ad tag from referral data, customer payload, message, or cached state.
    Returns a clean formatted string for LEAD DETAILS.
    """
    if isinstance(referral, str):
        try:
            referral = json.loads(referral)
        except Exception:
            referral = None

    ad_id = None
    ad_tag = None
    source_type = None

    # 1. Inspect referral object
    if referral and isinstance(referral, dict):
        ad_id = referral.get("ad_id") or referral.get("source_id")
        source_url = str(referral.get("source_url") or "")
        if not ad_id and source_url:
            match = re.search(r"ad_id[=:]([0-9a-zA-Z_]+)", source_url)
            if match:
                ad_id = match.group(1)

        ad_tag = (
            referral.get("ad_tag")
            or referral.get("tag")
            or referral.get("tags")
            or referral.get("headline")
            or referral.get("title")
        )
        source_type = referral.get("source_type")

    # 2. Inspect customer object
    if customer and isinstance(customer, dict):
        traits = customer.get("traits")
        if isinstance(traits, dict):
            if not ad_id:
                ad_id = traits.get("ad_id") or traits.get("source_id")
            if not ad_tag:
                ad_tag = traits.get("ad_tag") or traits.get("tag")

        if not ad_tag and customer.get("tags"):
            tags = customer.get("tags")
            if isinstance(tags, list) and tags:
                ad_tag = ", ".join(str(t) for t in tags)
            elif isinstance(tags, str):
                ad_tag = tags

    # 3. Inspect message object
    if message and isinstance(message, dict):
        if not ad_tag:
            ad_tag = message.get("ad_tag") or message.get("tag")

    # 4. Fallback to state cache if available
    if not state and phone:
        try:
            from src.state.chat_state import get_user_state
            state = get_user_state(phone)
        except Exception:
            state = None

    if state and isinstance(state, dict):
        if not ad_id and state.get("ad_id"):
            ad_id = state.get("ad_id")
        if not ad_tag and state.get("ad_tag"):
            ad_tag = state.get("ad_tag")

    # Check whitelisted test numbers
    allowed_raw = os.getenv("ALLOWED_TEST_NUMBERS", "").strip()
    is_whitelisted = False
    if allowed_raw and phone:
        allowed_test = [p.strip().replace("+", "") for p in allowed_raw.split(",") if p.strip()]
        clean_phone = phone.replace("+", "").strip()
        if clean_phone in allowed_test:
            is_whitelisted = True

    # 5. Build formatted details string
    parts = []
    if ad_id:
        parts.append(f"Ad ID: {ad_id}")
    else:
        parts.append("Ad ID: None")

    if ad_tag:
        parts.append(f"Ad Tag: {ad_tag}")
    elif is_whitelisted and not referral:
        parts.append("Ad Tag: Whitelisted Tester")
    elif state and state.get("is_target_ad") and not referral:
        parts.append("Ad Tag: None (Returning Verified Lead)")
    elif not referral:
        parts.append("Ad Tag: None (Organic Lead)")
    else:
        parts.append("Ad Tag: None")

    if source_type and source_type != "ad":
        parts.append(f"Source: {source_type}")

    return " | ".join(parts)


def log_lead_action(lead_no: str, lead_details: str, action_taken: str, reason: str):
    """
    Outputs the structured console log in the exact required format:
    ------------------------------------------------------------------------------------------------------------
    ----------------------------------------------------------------------------------------------------------
    LEAD NO: <number received>
    LEAD DETAILS: <ad id, ad tag>
    ACTION TAKEN: <REPLIED or IGNORED>
    REASON: <reason for decision>
    ------------------------------------------------------------------------------------------------------------
    """
    line1 = "-" * 108
    line2 = "-" * 106
    print(f"\n{line1}", flush=True)
    print(f"{line2}", flush=True)
    print(f"LEAD NO: {lead_no or 'Unknown'}", flush=True)
    print(f"LEAD DETAILS: {lead_details or 'N/A'}", flush=True)
    print(f"ACTION TAKEN: {action_taken}", flush=True)
    print(f"REASON: {reason}", flush=True)
    print(f"{line1}\n", flush=True)
