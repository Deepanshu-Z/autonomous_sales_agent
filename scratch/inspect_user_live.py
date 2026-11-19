import sys
import os
sys.path.insert(0, os.path.abspath("."))
import asyncio
from dotenv import load_dotenv

load_dotenv()

from src.services.supabase_client import supabase
from src.state.chat_state import get_user_state

async def inspect():
    phone = "917678368328"
    state = get_user_state(phone)
    print("--- LIVE USER STATE FOR 917678368328 ---")
    for k, v in state.items():
        if k in ["class_demo_video_sent", "trial_video_sent", "app_download_video_sent", "course", "stage", "payment_status", "is_escalated", "_last_turn"]:
            print(f"{k}: {v}")

if __name__ == "__main__":
    asyncio.run(inspect())
