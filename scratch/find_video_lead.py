import sys
import os
sys.path.insert(0, os.path.abspath("."))
import asyncio
from dotenv import load_dotenv

load_dotenv()

from src.services.supabase_client import supabase

async def find_exact_chat():
    res = supabase.table("chat_history").select("id, phone, role, message, created_at").ilike("message", "%can u send me class video%").execute()
    data = res.data or []
    print("Found matching messages:", len(data))
    for r in data:
        print(r)

if __name__ == "__main__":
    asyncio.run(find_exact_chat())
