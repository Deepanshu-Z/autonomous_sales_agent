import sys
import os
sys.path.insert(0, os.path.abspath("."))
import asyncio
from dotenv import load_dotenv

load_dotenv()

from src.services.interakt import send_video_message_async
from src.core import kb_loader

async def test_send():
    phone = "917678368328"
    course_data = kb_loader.get_course_data("kathak")
    url = course_data.get("course_overview", {}).get("class_raw_video_url")
    print("Testing send_video_message_async with URL:", url)
    caption = "🎬 Online Live Interactive Kathak live class practice video"
    success = await send_video_message_async(phone, url, caption=caption)
    print("Success:", success)

if __name__ == "__main__":
    asyncio.run(test_send())
