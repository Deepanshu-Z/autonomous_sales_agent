import sys, os, json
sys.path.insert(0, os.path.abspath('.'))
import asyncio
from dotenv import load_dotenv
load_dotenv()

from src.core.conversation_handler import dispatch_payment_qr_async
from src.state.chat_state import get_user_state

async def test_dispatch():
    phone = "919811134113"
    st = get_user_state(phone)
    print("Calling dispatch_payment_qr_async...")
    res = await dispatch_payment_qr_async(
        phone,
        st,
        course_id="ladies_dance",
        package="1 Month",
        force_resend=True
    )
    print("Dispatch result:", res)

asyncio.run(test_dispatch())
