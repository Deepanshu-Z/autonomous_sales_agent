import sys
import os
sys.path.insert(0, os.path.abspath("."))
import asyncio
from dotenv import load_dotenv

load_dotenv()

from src.services.supabase_client import supabase

async def check_recent_chat():
    res = supabase.table("chat_history").select("id, phone, role, message, created_at").gte("created_at", "2026-11-19T11:20:00Z").lte("created_at", "2026-11-19T11:35:00Z").order("created_at").execute()
    data = res.data or []
    print(f"Messages between 16:50 and 17:05 IST (11:20-11:35 UTC): {len(data)}")
    for r in data:
        print(f"[{r['created_at']}] {r['phone']} ({r['role']}): {r['message'][:100]}")

if __name__ == "__main__":
    asyncio.run(check_recent_chat())
