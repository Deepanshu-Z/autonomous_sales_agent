"""
whitelist_test_user.py — Whitelist a phone number for direct WhatsApp testing.

Bypasses the Meta Ad ID check (TARGET_AD_ID) so you can test the bot
in real-time directly from your personal WhatsApp without clicking a live Facebook/Instagram Ad.

Usage:
    python scripts/whitelist_test_user.py 919876543210
"""

import sys
import os

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Add workspace directory to python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.state.chat_state import get_user_state, save_user_state, clear_escalation

def normalize_phone(phone: str) -> str:
    clean = str(phone).strip().replace("+", "").replace(" ", "").replace("-", "")
    if len(clean) == 10:
        clean = "91" + clean
    return clean

def whitelist_phone(phone: str):
    phone_clean = normalize_phone(phone)
    print(f"\n🔍 Whitelisting phone number: {phone_clean} for WhatsApp testing...")

    # 1. Fetch current state
    state = get_user_state(phone_clean)

    # 2. Mark target ad verified & unescalate
    state["is_target_ad"] = True
    state["is_escalated"] = False
    save_user_state(phone_clean, state)
    clear_escalation(phone_clean)

    print("=" * 65)
    print(f"✅ SUCCESS: {phone_clean} is now whitelisted for real-time testing!")
    print(f"   • is_target_ad = True  (Bypasses Meta Ad filter)")
    print(f"   • is_escalated = False (AI automation enabled)")
    print("=" * 65)
    print("\n👉 Now send any message (e.g. 'Hi') from this WhatsApp number to your")
    print("   Interakt WhatsApp Business number and the bot will reply immediately!\n")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("\n❌ Error: Please provide your WhatsApp phone number with country code.")
        print("Usage:")
        print("    python scripts/whitelist_test_user.py 919876543210\n")
        sys.exit(1)

    whitelist_phone(sys.argv[1])
