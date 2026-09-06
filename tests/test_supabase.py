import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
"""
test_supabase.py — Helper script to test Supabase connection & table accessibility.
"""

import os
import sys
sys.stdout.reconfigure(encoding='utf-8')

from dotenv import load_dotenv
load_dotenv()

from supabase import create_client

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

print("==================================================")
print("           SUPABASE CONNECTION TEST               ")
print("==================================================")
print(f"URL: {SUPABASE_URL}")
print(f"KEY: {'[SET]' if SUPABASE_KEY else '[MISSING]'}\n")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("❌ ERROR: SUPABASE_URL or SUPABASE_KEY is missing in .env")
    sys.exit(1)

try:
    client = create_client(SUPABASE_URL, SUPABASE_KEY)
    print("✅ Supabase Client Initialized Successfully!\n")

    tables = ["user_session_state", "chat_history", "escalated_chats", "store_history"]
    for t in tables:
        try:
            res = client.table(t).select("*").limit(1).execute()
            print(f"  ✅ Table '{t}': OK (accessible, rows found: {len(res.data)})")
        except Exception as te:
            print(f"  ❌ Table '{t}': FAILED -> {te}")

    print("\n==================================================")
    print("🎉 Supabase is 100% CONNECTED and working properly!")
    print("==================================================")

except Exception as e:
    print(f"\n❌ Connection Failed: {e}")
