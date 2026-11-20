import sys
import os
sys.path.insert(0, os.path.abspath("."))
import asyncio
from dotenv import load_dotenv

load_dotenv()

from src.services.supabase_client import supabase
from src.state.chat_state import get_user_state

res = supabase.table("user_session_state").select("*").eq("phone", "917678368328").execute()
print("Supabase row:", res.data[0] if res.data else None)

state = get_user_state("917678368328")
print("\nRedis/Memory state stage:", state.get("stage"))
print("Redis/Memory state wants_trial:", state.get("wants_trial"))
print("Redis/Memory state _last_turn wants_trial:", (state.get("_last_turn") or {}).get("wants_trial"))
