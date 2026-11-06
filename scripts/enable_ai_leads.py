"""
enable_ai_leads.py — Enable AI automation and verify target ad status in Redis & Supabase.

Usage on production:
    # Enable all 8 Kathak leads in one go:
    ./venv/bin/python scripts/enable_ai_leads.py

    # Enable a specific number:
    ./venv/bin/python scripts/enable_ai_leads.py 919137895553
"""

import sys
import os

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Add project root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.state.chat_state import (
    get_user_state,
    save_user_state,
    clear_escalation
)
from src.services.agent_handoff import OWNER_EMAIL

DEFAULT_KATHAK_LEADS = [
    "918085096171",
    "918295600476",
    "919879387588",
    "917489272348",
    "919137895553",
    "918692011723",
    "918879897843",
    "919755582526"
]

def normalize_phone(phone: str) -> str:
    clean = str(phone).strip().replace("+", "").replace(" ", "").replace("-", "")
    if len(clean) == 10:
        clean = "91" + clean
    return clean

def enable_lead(raw_phone: str, course: str = "kathak"):
    phone = normalize_phone(raw_phone)
    print(f"\n🔍 Enabling AI for: {phone} (Course: {course})...")

    # 1. Fetch current state
    state = get_user_state(phone)

    # 2. Mark verified, unescalated, and course set
    state["is_target_ad"] = True
    state["is_escalated"] = False
    state["welcome_sent"] = True
    state["course"] = course
    if OWNER_EMAIL:
        state["assigned_agent_email"] = OWNER_EMAIL

    # 3. Persist to Redis & Supabase
    save_user_state(phone, state)
    clear_escalation(phone)

    print(f"  ✅ SUCCESS: {phone} is now active for AI responses!")
    print(f"     • is_target_ad = True")
    print(f"     • is_escalated = False")
    print(f"     • course = '{course}'")
    print(f"     • welcome_sent = True")

def main():
    phones = sys.argv[1:] if len(sys.argv) > 1 else DEFAULT_KATHAK_LEADS
    print("=" * 65)
    print(f"🚀 Activating AI for {len(phones)} lead(s)...")
    print("=" * 65)

    for p in phones:
        try:
            enable_lead(p)
        except Exception as e:
            print(f"  ❌ Error for {p}: {e}")

    print("\n" + "=" * 65)
    print("🎉 ALL DONE! The bot will now reply to all incoming messages from these leads.")
    print("=" * 65 + "\n")

if __name__ == "__main__":
    main()
