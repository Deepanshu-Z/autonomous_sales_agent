import sys
import os
sys.path.insert(0, os.path.abspath("."))
import asyncio
from dotenv import load_dotenv

load_dotenv()

from src.services.supabase_client import supabase

async def dump_state():
    phone = "917678368328"
    res = supabase.table("user_session_state").select("*").eq("phone", phone).execute()
    data = res.data or []
    if data:
        for k, v in data[0].items():
            print(f"{k}: {v}")
    else:
        print("No state found")

if __name__ == "__main__":
    asyncio.run(dump_state())
