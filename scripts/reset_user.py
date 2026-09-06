"""
reset_user.py — Complete reset utility for user history and state in Redis & Supabase.

Usage:
  python reset_user.py <phone_number>               # Full wipe: deletes chat history, state, escalation & caches
  python reset_user.py <phone_number> --history-only # Deletes ONLY chat messages, preserves funnel stage
  python reset_user.py <phone_number> --state-only   # Resets ONLY funnel state to NEW, keeps chat messages
"""

import sys
import os
import subprocess

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# Self-bootstrap into project venv if running with system python missing dependencies
_workspace_dir = os.path.dirname(os.path.abspath(__file__))
_venv_python = os.path.join(_workspace_dir, "venv", "Scripts", "python.exe")
if os.path.exists(_venv_python) and sys.executable.lower() != os.path.abspath(_venv_python).lower():
    try:
        import dotenv
    except ImportError:
        res = subprocess.run([_venv_python, os.path.abspath(__file__)] + sys.argv[1:])
        sys.exit(res.returncode)

sys.path.append(os.path.dirname(_workspace_dir))
sys.path.append(_workspace_dir)

from src.state.chat_state import reset_user_state, supabase, _memory_sessions, _memory_lock
from src.services.redis_client import get_redis_connection

redis_conn = get_redis_connection()

def normalize_phone(phone: str) -> str:
    return str(phone).strip().replace("+", "").replace(" ", "").replace("-", "")

def reset_chat_history(phone: str):
    """Deletes conversation messages in both Supabase and Redis."""
    print(f"\n🗑️  [1/2] Deleting chat history for {phone}...")
    
    # 1. Supabase chat_history table
    if supabase:
        try:
            res = supabase.table("chat_history").delete().eq("phone", phone).execute()
            count = len(res.data) if res.data else 0
            print(f"  • Supabase: Deleted message record(s) from 'chat_history'")
        except Exception as e:
            print(f"  ❌ Supabase history delete error: {e}")
            
    # 2. Redis history cache
    try:
        redis_conn.delete(f"history:{phone}")
        print(f"  • Redis: Cleared key 'history:{phone}'")
    except Exception as e:
        print(f"  ❌ Redis history cache clear error: {e}")

def reset_session_state(phone: str):
    """Resets user state to default 'NEW' and clears locks/queues."""
    print(f"\n🔄 [2/2] Resetting session state, timers & locks for {phone}...")
    
    # 1. Clear in-memory cache
    with _memory_lock:
        _memory_sessions.pop(phone, None)
    print("  • Memory: Cleared active in-memory session")

    # 2. Clear Redis state, queues, locks & debouncers
    redis_keys = [
        f"user_state:{phone}",
        f"is_processing:{phone}",
        f"pending_queue:{phone}",
        f"phone-lock:{phone}",
        f"batch:{phone}",
        f"batch_token:{phone}",
        f"batch_timer:{phone}"
    ]
    try:
        for k in redis_keys:
            redis_conn.delete(k)
        print(f"  • Redis: Cleared {len(redis_keys)} state/lock/queue keys")
    except Exception as e:
        print(f"  ❌ Redis state clear error: {e}")

    # 3. Supabase session state & escalation
    if supabase:
        try:
            supabase.table("user_session_state").delete().eq("phone", phone).execute()
            print("  • Supabase: Cleared record from 'user_session_state'")
        except Exception as e:
            print(f"  ❌ Supabase user_session_state delete error: {e}")
        try:
            supabase.table("escalated_chats").delete().eq("phone", phone).execute()
            print("  • Supabase: Cleared record from 'escalated_chats'")
        except Exception as e:
            print(f"  ❌ Supabase escalated_chats delete error: {e}")

def reset_full_user(phone: str):
    """Completely resets everything for a phone number."""
    phone = normalize_phone(phone)
    print(f"\n{'='*70}")
    print(f"💥 FULL WIPE FOR PHONE NUMBER: {phone}")
    print(f"{'='*70}")
    
    reset_chat_history(phone)
    reset_session_state(phone)
    
    print(f"\n{'='*70}")
    print(f"✅ SUCCESS: {phone} is now completely fresh as a brand new lead!")
    print(f"   • Supabase: chat history, state & escalation wiped")
    print(f"   • Redis: history cache, state, queues & debouncer wiped")
    print(f"{'='*70}\n")

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        print("Example: python reset_user.py 917678368328")
        return

    phone = sys.argv[1].strip()
    flag = sys.argv[2].strip().lower() if len(sys.argv) > 2 else ""

    if flag == "--history-only":
        p = normalize_phone(phone)
        reset_chat_history(p)
        print(f"✅ Chat history wiped for {p}.")
    elif flag == "--state-only":
        p = normalize_phone(phone)
        reset_session_state(p)
        print(f"✅ Session state reset for {p}.")
    else:
        reset_full_user(phone)

if __name__ == "__main__":
    main()
