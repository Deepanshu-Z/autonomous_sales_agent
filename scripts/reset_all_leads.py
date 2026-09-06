import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
"""
reset_all_leads.py — Resets is_target_ad=False for ALL users in Supabase & Redis.
This makes the AI stop replying to every existing lead.
Only new leads coming with the target Meta Ad ID (TARGET_AD_ID) will be verified.
"""
import os
import sys
import json
import time
from dotenv import load_dotenv

load_dotenv()

from supabase import create_client
from src.services.redis_client import get_redis_connection

supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
redis_conn = get_redis_connection()

def reset_all_leads():
    print("=" * 70)
    print("  RESETTING ALL LEADS — Clearing is_target_ad for everyone")
    print("=" * 70)
    print()

    # 1. Fetch all users with is_target_ad = True from Supabase
    print("[1/3] Fetching all verified leads from Supabase...")
    try:
        result = supabase.table("user_session_state").select("phone, stage, is_target_ad").eq("is_target_ad", True).execute()
        leads = result.data if result.data else []
        print(f"  Found {len(leads)} verified lead(s) with is_target_ad=True")
        for lead in leads:
            print(f"    • {lead['phone']} (stage: {lead.get('stage', 'N/A')})")
    except Exception as e:
        print(f"  Error fetching leads: {e}")
        leads = []

    if not leads:
        print("\n  No leads to reset. Done!")
        return

    # 2. Update all users in Supabase: set is_target_ad = False
    print(f"\n[2/3] Setting is_target_ad=False for {len(leads)} lead(s) in Supabase...")
    reset_count = 0
    for lead in leads:
        phone = lead["phone"]
        try:
            supabase.table("user_session_state").update({
                "is_target_ad": False
            }).eq("phone", phone).execute()
            reset_count += 1
        except Exception as e:
            print(f"  Error resetting {phone}: {e}")
    print(f"  Supabase: Reset {reset_count}/{len(leads)} lead(s)")

    # 3. Clear Redis state cache for all those phones
    print(f"\n[3/3] Clearing Redis state cache for {len(leads)} lead(s)...")
    redis_cleared = 0
    for lead in leads:
        phone = lead["phone"]
        try:
            keys_to_clear = [
                f"user_state:{phone}",
                f"state:{phone}",
            ]
            for key in keys_to_clear:
                redis_conn.delete(key)
            redis_cleared += 1
        except Exception as e:
            print(f"  Error clearing Redis for {phone}: {e}")
    print(f"  Redis: Cleared cache for {redis_cleared}/{len(leads)} lead(s)")

    print()
    print("=" * 70)
    print(f"  SUCCESS: {reset_count} lead(s) reset. AI will NOT reply to them.")
    print(f"  Only users arriving from TARGET_AD_ID will enable AI.")
    print("=" * 70)


if __name__ == "__main__":
    reset_all_leads()
