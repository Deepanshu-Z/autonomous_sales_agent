"""
unescalate.py — Command-line tool to unescalate phone numbers and re-enable AI automation.

Usage:
  python unescalate.py <phone_number>      # Unescalate a single phone number (e.g. 917678368328)
  python unescalate.py --all               # Unescalate ALL escalated phone numbers
  python unescalate.py --list              # List all currently escalated phone numbers
  python unescalate.py --reset <phone>     # Completely reset all session data for a phone number
"""

import sys
import os

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.state.chat_state import clear_escalation, reset_user_state, supabase, get_user_state

def list_escalated():
    print("\n🔍 Fetching escalated leads...")
    if not supabase:
        print("⚠️ Supabase client not configured.")
        return []
    try:
        res = supabase.table("escalated_chats").select("*").execute()
        rows = res.data or []
        print(f"📋 Total escalated numbers in database: {len(rows)}")
        for r in rows:
            print(f"  • Phone: {r.get('phone')} (Escalated at: {r.get('created_at', 'N/A')})")
        return rows
    except Exception as e:
        print(f"❌ Error fetching escalated chats: {e}")
        return []

def unescalate_phone(phone: str):
    phone = phone.strip().replace("+", "").replace(" ", "").replace("-", "")
    print(f"\n🔓 Unescalating phone: {phone}...")
    clear_escalation(phone)
    print(f"✅ Successfully unescalated {phone}! AI automated replies are now RE-ENABLED for this number.")

def unescalate_all():
    print("\n🔓 Unescalating ALL leads...")
    if not supabase:
        print("⚠️ Supabase client not configured.")
        return
    try:
        res = supabase.table("escalated_chats").select("phone").execute()
        rows = res.data or []
        if not rows:
            print("ℹ️ No escalated chats found.")
            return
        for r in rows:
            p = r.get("phone")
            if p:
                clear_escalation(p)
                print(f"  • Unescalated: {p}")
        print(f"✅ Successfully unescalated all {len(rows)} lead(s)! AI replies re-enabled.")
    except Exception as e:
        print(f"❌ Error during bulk unescalate: {e}")

def reset_phone(phone: str):
    phone = phone.strip().replace("+", "").replace(" ", "").replace("-", "")
    print(f"\n🔄 Completely resetting session state for phone: {phone}...")
    reset_user_state(phone)
    print(f"✅ Successfully reset {phone}! Session wiped clean and AI re-enabled as a new lead.")

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        list_escalated()
        return

    arg = sys.argv[1].strip()

    if arg in ["--list", "-l", "list"]:
        list_escalated()
    elif arg in ["--all", "-a", "all"]:
        unescalate_all()
    elif arg in ["--reset", "-r", "reset"] and len(sys.argv) >= 3:
        reset_phone(sys.argv[2])
    else:
        unescalate_phone(arg)

if __name__ == "__main__":
    main()
