import sys
import os
sys.path.insert(0, os.path.abspath("."))
import asyncio
from dotenv import load_dotenv

load_dotenv()

from src.services.supabase_client import supabase

res = supabase.table("chat_history").select("id, phone, role, message, created_at").ilike("message", "%trial class book karna chahenge%").order("created_at", desc=True).limit(10).execute()
print("Found count:", len(res.data or []))
for r in (res.data or []):
    print(f"[{r['created_at']}] {r['phone']}: {r['message']}")
