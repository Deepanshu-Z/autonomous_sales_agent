"""
followup_worker.py — standalone background process. Polls Supabase every
30 seconds for customers who haven't replied within their follow-up window,
sends a reminder (or escalates on the second miss), fully independent of
the main webhook/queue pipeline since nothing "arrives" to trigger this.

High-Performance Concurrency:
- Controlled concurrent pool via asyncio.Semaphore (default: 10 parallel leads).
- Bounded batch query via .limit(BATCH_SIZE) (default: 100 leads per sweep).
- Prevents head-of-line blocking while strictly respecting WhatsApp / Interakt rate limits.

Deploy as its own Render Background Worker (separate from the message
queue worker) with: python followup_worker.py
"""

import os
import sys
# Ensure project root is in sys.path when executed directly as a script
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True, encoding="utf-8")
import time
import asyncio
from datetime import datetime, timezone
from dotenv import load_dotenv
from supabase import create_client

from src.services.interakt import send_text_message_async, assign_chat_to_agent_async
from src.state.chat_state import mark_escalated_async, is_at_qr_stage
from src.state.chat_history import save_message_async
from src.state.csv_logger import log_message_async
from src.services.redis_client import get_redis_connection
from src.services.agent_summary import send_agent_summary_async
from src.services.agent_handoff import get_next_agent_email

load_dotenv()

supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
redis_conn = get_redis_connection()

POLL_INTERVAL = int(os.getenv("FOLLOWUP_INTERVAL", 30))
MAX_CONCURRENCY = int(os.getenv("FOLLOWUP_CONCURRENCY", 10))
BATCH_SIZE = int(os.getenv("FOLLOWUP_BATCH_SIZE", 100))


async def process_single_lead(row: dict, now_ts: float):
    phone = row["phone"]
    count = row.get("follow_up_count", 0)
    topic = row.get("last_topic") or "your query"

    # Per-phone lock so this sweeper never races the main message
    # pipeline if the customer replies at the exact same moment.
    lock = redis_conn.lock(f"phone-lock:{phone}", timeout=30, blocking_timeout=2)
    acquired = await asyncio.to_thread(lock.acquire, False)
    if not acquired:
        return

    try:
        from src.state.chat_state import get_user_state, save_user_state, is_at_qr_stage
        paid_state = await asyncio.to_thread(get_user_state, phone)
        if paid_state.get("payment_status") == "PAID" or paid_state.get("stage") == "ENROLLED" or paid_state.get("is_enrolled"):
            paid_state["follow_up_count"] = 0
            paid_state["next_followup_due_at"] = None
            paid_state["next_follow_up_due_at"] = None
            await asyncio.to_thread(save_user_state, phone, paid_state)
            await asyncio.to_thread(
                lambda: supabase.table("user_session_state").update({
                    "next_followup_due_at": None,
                    "follow_up_count": 0,
                }).eq("phone", phone).execute()
            )
            return

        lead_at_qr = is_at_qr_stage(paid_state)
        if count == 0:
            from src.state.chat_history import get_recent_history
            from src.core import kb_loader
            state = paid_state
            course_id = (state.get("course") or "").strip()
            if not course_id:
                hist = await asyncio.to_thread(get_recent_history, phone, 6)
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
            await send_text_message_async(phone, reply)
            await save_message_async(phone, "assistant", reply)
            await log_message_async(phone, "ai", reply)

            if lead_at_qr:
                # Leads at QR stage only get the 5-min follow-up reminder.
                # No agent handoff is scheduled. Lead stays with owner and AI remains 100% active.
                await asyncio.to_thread(
                    lambda: supabase.table("user_session_state").update({
                        "follow_up_count": 1,
                        "next_followup_due_at": None,
                        "is_escalated": False,
                    }).eq("phone", phone).execute()
                )
                try:
                    st = await asyncio.to_thread(get_user_state, phone)
                    st["follow_up_count"] = 1
                    st["next_followup_due_at"] = None
                    st["is_escalated"] = False
                    await asyncio.to_thread(save_user_state, phone, st)
                except Exception:
                    pass
                print(f"[followup_worker] {phone}: sent 5-min reminder for QR lead — no agent handoff scheduled, AI remains active.")
            else:
                # Arm the second window: 30 minutes (1800s) before escalating to agent
                next_due_iso = datetime.fromtimestamp(now_ts + 1800, tz=timezone.utc).isoformat()
                await asyncio.to_thread(
                    lambda: supabase.table("user_session_state").update({
                        "follow_up_count": 1,
                        "next_followup_due_at": next_due_iso,
                    }).eq("phone", phone).execute()
                )
                try:
                    st = await asyncio.to_thread(get_user_state, phone)
                    st["follow_up_count"] = 1
                    st["next_followup_due_at"] = now_ts + 1800
                    await asyncio.to_thread(save_user_state, phone, st)
                except Exception:
                    pass
                print(f"[followup_worker] {phone}: sent 1st reminder — scheduled agent assignment in 30 mins")

        elif count == 1:
            if lead_at_qr:
                # Fail-safe: QR leads must never be escalated or reassigned to staff on silence.
                print(f"[followup_worker] {phone}: QR lead reached count 1 — skipping escalation. AI stays active.")
                await asyncio.to_thread(
                    lambda: supabase.table("user_session_state").update({
                        "next_followup_due_at": None,
                        "is_escalated": False,
                    }).eq("phone", phone).execute()
                )
                try:
                    st = await asyncio.to_thread(get_user_state, phone)
                    st["next_followup_due_at"] = None
                    st["is_escalated"] = False
                    await asyncio.to_thread(save_user_state, phone, st)
                except Exception:
                    pass
                return

            # 2nd consecutive unanswered follow-up -> escalate to human agent
            agent, agent_index = get_next_agent_email()
            reply = "Connecting you with our team now. Someone will be with you shortly!"
            if agent:
                await assign_chat_to_agent_async(phone, agent)
            await send_text_message_async(phone, reply)
            await mark_escalated_async(phone)
            await save_message_async(phone, "assistant", reply)
            await log_message_async(phone, "agent", reply)

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
            .order("next_followup_due_at", desc=False)
            .limit(BATCH_SIZE)
            .execute()
        )
    except Exception as e:
        print(f"[followup_worker] Query failed: {e}")
        return

    data = result.data or []
    print(f"[followup_worker] Fetched {len(data)} user(s) due for follow-up (batch limit: {BATCH_SIZE})")
    if not data:
        return

    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

    async def worker_with_semaphore(row):
        async with semaphore:
            await process_single_lead(row, now_ts)

    t0 = time.time()
    await asyncio.gather(*(worker_with_semaphore(r) for r in data), return_exceptions=True)
    t1 = time.time()

    elapsed = t1 - t0
    rate = len(data) / elapsed if elapsed > 0 else 0
    print(f"[followup_worker] Sweep finished in {elapsed:.2f}s ({len(data)} leads, {rate:.1f} leads/sec, concurrency={MAX_CONCURRENCY})")


async def main_loop():
    print(f"[followup_worker] Started — polling every {POLL_INTERVAL}s (concurrency={MAX_CONCURRENCY}, batch_size={BATCH_SIZE})")
    cycle = 0
    while True:
        cycle += 1
        print(f"\n[followup_worker] --- Cycle #{cycle} ---")
        await sweep_once()
        print(f"[followup_worker] Sleeping {POLL_INTERVAL}s until next cycle...\n")
        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main_loop())
