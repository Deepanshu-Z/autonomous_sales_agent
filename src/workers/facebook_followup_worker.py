"""
facebook_followup_worker.py — Dedicated standalone worker for Facebook Messenger follow-ups.
Polls Supabase every 30 seconds strictly for Facebook users (phone LIKE 'fb_%').
Handles Meta Messenger policies, delivery checks, and human agent handoff.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True, encoding="utf-8")
import time
import asyncio
from datetime import datetime, timezone
from dotenv import load_dotenv
from supabase import create_client

from src.services.interakt import send_text_message_async, assign_chat_to_agent_async
from src.state.chat_state import mark_escalated_async
from src.state.chat_history import save_message_async
from src.state.csv_logger import log_message_async
from src.services.redis_client import get_redis_connection
from src.services.agent_handoff import get_next_agent_email
from src.core import kb_loader

load_dotenv()

supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
redis_conn = get_redis_connection()

POLL_INTERVAL = int(os.getenv("FACEBOOK_FOLLOWUP_INTERVAL", 30))


async def sweep_once():
    now_ts = time.time()
    now_iso = datetime.now(timezone.utc).isoformat()
    print(f"[facebook_followup] Sweep started at {time.strftime('%H:%M:%S')}")
    try:
        result = await asyncio.to_thread(
            lambda: supabase.table("user_session_state")
            .select("phone, follow_up_count, next_followup_due_at, last_topic, is_escalated")
            .like("phone", "fb_%")
            .lte("next_followup_due_at", now_iso)
            .lt("follow_up_count", 2)
            .eq("is_escalated", False)
            .execute()
        )
    except Exception as e:
        print(f"[facebook_followup] Query failed: {e}")
        return

    print(f"[facebook_followup] Fetched {len(result.data)} Facebook user(s) due for follow-up")

    for row in result.data:
        phone = row["phone"]
        count = row.get("follow_up_count", 0)

        lock = redis_conn.lock(f"phone-lock:{phone}", timeout=30, blocking_timeout=2)
        acquired = await asyncio.to_thread(lock.acquire, False)
        if not acquired:
            continue

        try:
            if count == 0:
                from src.state.chat_state import get_user_state
                from src.state.chat_history import get_recent_history
                state = get_user_state(phone)
                course_id = (state.get("course") or "").strip()
                if not course_id:
                    hist = get_recent_history(phone, limit=6)
                    if hist:
                        hist_text = " ".join((t.get("content") or "") for t in hist)
                        detected_c = kb_loader.detect_course_from_text(hist_text, default_course=None)
                        if detected_c:
                            course_id = detected_c

                if course_id:
                    course_data = kb_loader.get_course_data(course_id)
                    course_name = (course_data.get("course") or course_id.replace("_", " ")).title()
                    reply = (
                        f"Hi again! 😊 Just checking in — were you still interested in "
                        f"knowing more about our {course_name} classes? Happy to help whenever you're ready!"
                    )
                else:
                    reply = (
                        "Hi again! 😊 Just checking in — were you still interested in "
                        "exploring our classes? Happy to help whenever you're ready!"
                    )

                res = await send_text_message_async(phone, reply)
                is_failed = isinstance(res, dict) and "error" in res

                if is_failed:
                    err_msg = res.get("error")
                    print(f"[facebook_followup] ⚠️ Meta API delivery rejected for {phone}: {err_msg}")
                    await asyncio.to_thread(
                        lambda: supabase.table("user_session_state").update({
                            "next_followup_due_at": None,
                        }).eq("phone", phone).execute()
                    )
                else:
                    await save_message_async(phone, "assistant", reply)
                    await log_message_async(phone, "ai", reply)

                    next_due_iso = datetime.fromtimestamp(now_ts + 1800, tz=timezone.utc).isoformat()
                    await asyncio.to_thread(
                        lambda: supabase.table("user_session_state").update({
                            "follow_up_count": 1,
                            "next_followup_due_at": next_due_iso,
                        }).eq("phone", phone).execute()
                    )
                    try:
                        from src.state.chat_state import get_user_state, save_user_state
                        st = get_user_state(phone)
                        st["follow_up_count"] = 1
                        st["next_followup_due_at"] = now_ts + 1800
                        save_user_state(phone, st)
                    except Exception:
                        pass
                    print(f"[facebook_followup] {phone}: sent 1st reminder — scheduled agent assignment in 30 mins")

            elif count == 1:
                agent, agent_index = get_next_agent_email()
                reply = "Connecting you with our team now. Someone will be with you shortly!"
                if agent:
                    await assign_chat_to_agent_async(phone, agent)
                res = await send_text_message_async(phone, reply)
                is_failed = isinstance(res, dict) and "error" in res
                if not is_failed:
                    await save_message_async(phone, "assistant", reply)
                    await log_message_async(phone, "agent", reply)
                await mark_escalated_async(phone)

                await asyncio.to_thread(
                    lambda: supabase.table("user_session_state").update({
                        "follow_up_count": 2,
                        "next_followup_due_at": None,
                        "is_escalated": True,
                    }).eq("phone", phone).execute()
                )
                print(f"[facebook_followup] {phone}: escalated to human agent after 2 silences")

        except Exception as e:
            print(f"[facebook_followup] Error processing {phone}: {e}")
        finally:
            try:
                await asyncio.to_thread(lock.release)
            except Exception:
                pass

    print(f"[facebook_followup] Sweep finished at {time.strftime('%H:%M:%S')}")


async def main_loop():
    print(f"[facebook_followup] Started — polling every {POLL_INTERVAL}s")
    cycle = 0
    while True:
        cycle += 1
        print(f"\n[facebook_followup] --- Cycle #{cycle} ---")
        await sweep_once()
        print(f"[facebook_followup] Sleeping {POLL_INTERVAL}s until next cycle...\n")
        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main_loop())
