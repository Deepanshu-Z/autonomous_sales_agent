"""
followup_worker.py — standalone background process. Polls Supabase every
30 seconds for customers who haven't replied within their follow-up window,
sends a reminder (or escalates on the second miss), fully independent of
the main webhook/queue pipeline since nothing "arrives" to trigger this.

Deploy as its own Render Background Worker (separate from the message
queue worker) with: python followup_worker.py
"""

import os
import time
import asyncio
from dotenv import load_dotenv
from supabase import create_client

from src.services.interakt import send_text_message_async, assign_chat_to_agent_async
from src.state.chat_state import mark_escalated_async
from src.state.chat_history import save_message_async
from src.state.csv_logger import log_message_async
from src.services.redis_client import get_redis_connection
from src.services.agent_summary import send_agent_summary_async

load_dotenv()

supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
redis_conn = get_redis_connection()

POLL_INTERVAL = 30  # seconds between sweeps
PRIORITY_AGENT_EMAIL_ANOTHER_1 = os.getenv("PRIORITY_AGENT_EMAIL_ANOTHER_1")
PRIORITY_AGENT_EMAIL_ANOTHER_2 = os.getenv("PRIORITY_AGENT_EMAIL_ANOTHER_2")
AGENT_POOL = [e for e in [PRIORITY_AGENT_EMAIL_ANOTHER_1, PRIORITY_AGENT_EMAIL_ANOTHER_2] if e]


def get_next_agent_email() -> tuple:
    if not AGENT_POOL:
        return os.getenv("PRIORITY_AGENT_EMAIL"), 0
    counter = redis_conn.incr("agent_round_robin_counter")
    index = (counter - 1) % len(AGENT_POOL)
    return AGENT_POOL[index], index


from datetime import datetime, timezone

async def sweep_once():
    now_ts = time.time()
    now_iso = datetime.now(timezone.utc).isoformat()
    print(f"[followup_worker] Sweep started at {time.strftime('%H:%M:%S')}")
    try:
        result = await asyncio.to_thread(
            lambda: supabase.table("user_session_state")
            .select("phone, follow_up_count, next_followup_due_at, last_topic, is_escalated")
            .lte("next_followup_due_at", now_iso)
            .lt("follow_up_count", 2)
            .eq("is_escalated", False)
            .execute()
        )
    except Exception as e:
        print(f"[followup_worker] Query failed: {e}")
        return
    print(f"[followup_worker] Fetched {len(result.data)} user(s) due for follow-up")

    for row in result.data:
        phone = row["phone"]
        count = row.get("follow_up_count", 0)
        topic = row.get("last_topic") or "your query"

        # Per-phone lock so this sweeper never races the main message
        # pipeline if the customer replies at the exact same moment.
        lock = redis_conn.lock(f"phone-lock:{phone}", timeout=30, blocking_timeout=2)
        acquired = await asyncio.to_thread(lock.acquire, False)
        if not acquired:
            continue

        try:
            if count == 0:
                from src.state.chat_state import get_user_state
                from src.core import kb_loader
                state = get_user_state(phone)
                course_id = state.get("course") or "yoga"
                course_data = kb_loader.get_course_data(course_id)
                course_name = course_data.get("course", course_id.title())

                reply = (
                    f"Hi again! 😊 Just checking in — were you still interested in "
                    f"knowing more about our {course_name} classes? Happy to help whenever you're ready!"
                )
                await send_text_message_async(phone, reply)
                await save_message_async(phone, "assistant", reply)
                await log_message_async(phone, "ai", reply)

                next_due_iso = datetime.fromtimestamp(now_ts + 300, tz=timezone.utc).isoformat()
                await asyncio.to_thread(
                    lambda: supabase.table("user_session_state").update({
                        "follow_up_count": 1,
                        "next_followup_due_at": next_due_iso,  # arm the 5-min window again
                    }).eq("phone", phone).execute()
                )
                print(f"[followup_worker] {phone}: sent 1st reminder")

            elif count == 1:
                # 2nd consecutive unanswered follow-up -> escalate to human agent
                agent, agent_index = get_next_agent_email()
                reply = "Connecting you with our team now. Someone will be with you shortly!"
                if agent:
                    await assign_chat_to_agent_async(phone, agent)
                await send_text_message_async(phone, reply)
                await mark_escalated_async(phone)
                await save_message_async(phone, "assistant", reply)
                await log_message_async(phone, "agent", reply)
                # Send Hinglish summary to the assigned agent's WhatsApp number
                asyncio.create_task(
                    send_agent_summary_async(
                        phone, agent_index,
                        escalation_reason="2 follow-up messages ka jawab nahi aaya (silence)"
                    )
                )

                await asyncio.to_thread(
                    lambda: supabase.table("user_session_state").update({
                        "follow_up_count": 2,
                        "next_followup_due_at": None,
                        "is_escalated": True,
                    }).eq("phone", phone).execute()
                )
                print(f"[followup_worker] {phone}: escalated after 2 consecutive silences")

        except Exception as e:
            print(f"[followup_worker] Error processing {phone}: {e}")
        finally:
            try:
                await asyncio.to_thread(lock.release)
            except Exception:
                pass

    print(f"[followup_worker] Sweep finished at {time.strftime('%H:%M:%S')}")


async def main_loop():
    print(f"[followup_worker] Started — polling every {POLL_INTERVAL}s")
    cycle = 0
    while True:
        cycle += 1
        print(f"\n[followup_worker] --- Cycle #{cycle} ---")
        await sweep_once()
        print(f"[followup_worker] Sleeping {POLL_INTERVAL}s until next cycle...\n")

        await asyncio.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    asyncio.run(main_loop())