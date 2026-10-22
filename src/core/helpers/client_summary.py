"""
client_summary.py — Utility to fetch and format real-time customer session summaries.

Used by:
1. WhatsApp in-chat command: #summary / /summary
2. Standalone CLI script: python get_summary.py <phone_number>
"""

import re
import time
from typing import Dict, Any, Optional, List

from src.state.chat_state import get_user_state_async, is_escalated_async
from src.state.chat_history import get_recent_history
from src.core import kb_loader


def normalize_phone_candidates(raw_phone: str) -> List[str]:
    """Generates all standard phone format candidates to lookup state."""
    clean = re.sub(r"\D", "", str(raw_phone or "").strip())
    candidates = []
    if not clean:
        return [raw_phone] if raw_phone else []

    if len(clean) == 10:
        candidates.extend([f"wa_91{clean}", f"91{clean}", clean])
    elif len(clean) == 12 and clean.startswith("91"):
        candidates.extend([f"wa_{clean}", clean, clean[2:]])
    elif clean.startswith("91") and len(clean) > 10:
        candidates.extend([f"wa_{clean}", clean])
    else:
        candidates.extend([f"wa_{clean}", clean])

    if raw_phone not in candidates:
        candidates.insert(0, raw_phone)
    return candidates


async def get_client_summary_async(raw_phone: str) -> str:
    """
    Fetches real-time session state, history, and escalation status for a customer,
    and returns a clean, beautifully formatted markdown summary.
    """
    state = await get_user_state_async(raw_phone)
    matched_phone = raw_phone
    if not any(state.get(k) for k in ("course", "timing", "package", "fee")):
        candidates = normalize_phone_candidates(raw_phone)
        for cand in candidates:
            if cand != raw_phone:
                s = await get_user_state_async(cand)
                if s and any(s.get(k) for k in ("course", "timing", "package", "fee")):
                    state = s
                    matched_phone = cand
                    break

    # Escalation check
    is_escalated = await is_escalated_async(matched_phone)

    # History retrieval
    history = get_recent_history(matched_phone, limit=10)
    if not history and matched_phone != raw_phone:
        history = get_recent_history(raw_phone, limit=10)

    last_user_msg = "None"
    last_ai_msg = "None"
    for turn in reversed(history):
        role = turn.get("role")
        content = (turn.get("content") or "").strip()
        if not content:
            continue
        if role in ("user", "customer") and last_user_msg == "None":
            last_user_msg = content[:100] + ("..." if len(content) > 100 else "")
        elif role in ("assistant", "ai") and last_ai_msg == "None":
            last_ai_msg = content[:100] + ("..." if len(content) > 100 else "")

    # Format fields
    clean_display_phone = re.sub(r"\D", "", matched_phone)
    display_phone = f"+{clean_display_phone}" if clean_display_phone else matched_phone

    cid = (state.get("course") or "Not Selected").strip()
    catalog_entry = kb_loader.get_catalog_entry(cid) if cid and cid != "Not Selected" else None
    course_title = catalog_entry.get("name") if catalog_entry else cid.title()

    stage = state.get("stage") or "NEW"
    timing = state.get("timing") or state.get("selected_timing") or "Not Selected"
    teacher = state.get("selected_teacher") or state.get("teacher") or "Not Selected"
    package = state.get("package") or state.get("selected_duration") or "Not Selected"

    fee = state.get("fee")
    fee_str = f"₹{fee:,}" if isinstance(fee, (int, float)) and fee > 0 else (str(fee) if fee else "N/A")

    payment_status = (state.get("payment_status") or "UNPAID").upper()
    paid_amt = state.get("paid_amount")
    paid_amt_str = f"₹{paid_amt}" if paid_amt is not None else "₹0.00"

    payment_id = state.get("payment_id") or state.get("order_id") or "None"
    app_installed = "✅ Yes" if state.get("app_installed") else "❌ Pending"
    profile_created = "✅ Yes" if state.get("profile_created") else "❌ Pending"
    coupon_sent = "✅ Yes" if state.get("coupon_sent") else "❌ No"
    language = state.get("language") or "English"
    ai_status = "🚨 Escalated to Human Agent" if is_escalated else "🟢 AI Assistant Active"

    summary_lines = [
        "📋 *CLIENT SESSION & PROFILE SUMMARY*",
        "━━━━━━━━━━━━━━━━━━━━",
        f"📱 *Phone:* {display_phone}",
        f"🎯 *Stage:* {stage}",
        f"📚 *Course:* {course_title}",
        f"⏰ *Batch Timing:* {timing}",
        f"👩‍🏫 *Instructor:* {teacher}",
        f"📦 *Package Duration:* {package}",
        f"💰 *Fee / Price:* {fee_str}",
        "────────────────────",
        f"💳 *Payment Status:* {payment_status}",
        f"💵 *Amount Paid:* {paid_amt_str}",
        f"🧾 *Transaction / Order ID:* {payment_id}",
        "────────────────────",
        f"📲 *App Installed:* {app_installed}",
        f"👤 *Profile Created:* {profile_created}",
        f"🎟️ *Coupon Sent:* {coupon_sent}",
        f"🌐 *Language:* {language}",
        f"🤖 *AI Agent Status:* {ai_status}",
        "────────────────────",
        "💬 *Recent Activity:*",
        f"• Messages Logged: {len(history)}",
        f"• Last User Message: \"{last_user_msg}\"",
        f"• Last AI Reply: \"{last_ai_msg}\"",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    return "\n".join(summary_lines)
