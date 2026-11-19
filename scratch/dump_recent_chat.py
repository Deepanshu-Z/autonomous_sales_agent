import sys
import os
sys.path.insert(0, os.path.abspath("."))
import asyncio
from dotenv import load_dotenv

load_dotenv()

from src.services.supabase_client import supabase

async def dump_recent():
    phone = "917678368328"
    res = supabase.table("chat_history").select("id, role, message, created_at").eq("phone", phone).gte("created_at", "2026-11-19T11:55:00Z").order("created_at").execute()
    data = res.data or []
    print(f"Messages after 17:25 IST for {phone}: {len(data)}")
    for r in data:
        print(f"[{r['created_at']}] {r['role'].upper()}: {r['message']}")

if __name__ == "__main__":
    asyncio.run(dump_recent())
