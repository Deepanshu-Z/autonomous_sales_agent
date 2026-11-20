import sys
import os
sys.path.insert(0, os.path.abspath("."))
import asyncio
from dotenv import load_dotenv

load_dotenv()

from src.services.supabase_client import supabase

res = supabase.table("chat_history").select("id, role, message, created_at").eq("phone", "917678368328").gte("created_at", "2026-11-19T12:10:00Z").order("created_at").execute()
for r in (res.data or []):
    print(f"[{r['created_at']}] {r['role'].upper()}: {r['message']}")
