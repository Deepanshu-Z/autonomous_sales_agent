"""
whatsapp_followup_worker.py — Dedicated standalone worker for WhatsApp follow-ups.
Polls Supabase every 30 seconds strictly for WhatsApp customers (excludes ig_ and fb_).
Implements a 24-hour factual follow-up cadence (5m, 30m, 3h, 9h, 15h, 21h) with
the 3 Free Live Trial Classes offer on the final follow-up, followed by agent handoff at 24h.
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
from src.state.chat_state import mark_escalated_async, is_at_qr_stage
from src.state.chat_history import save_message_async
from src.state.csv_logger import log_message_async
from src.services.redis_client import get_redis_connection
from src.services.agent_handoff import get_next_agent_email
from src.services.followup_service import get_followup_for_step, get_next_gap_seconds, TOTAL_STEPS
from src.core import kb_loader

load_dotenv()

supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
redis_conn = get_redis_connection()

POLL_INTERVAL = int(os.getenv("WHATSAPP_FOLLOWUP_INTERVAL", 30))


async def sweep_once():
    now_ts = time.time()
    now_iso = datetime.now(timezone.utc).isoformat()
    print(f"[whatsapp_followup] Sweep started at {time.strftime('%H:%M:%S')}")
    try:
        result = await asyncio.to_thread(
            lambda: supabase.table("user_session_state")
            .select("phone, follow_up_count, next_followup_due_at, last_topic, is_escalated")
            .not_.like("phone", "ig_%")
            .not_.like("phone", "fb_%")
            .lte("next_followup_due_at", now_iso)
            .lt("follow_up_count", TOTAL_STEPS + 1)
            .eq("is_escalated", False)
            .execute()
        )
    except Exception as e:
        print(f"[whatsapp_followup] Query failed: {e}")
        return

    print(f"[whatsapp_followup] Fetched {len(result.data)} WhatsApp user(s) due for follow-up")

    for row in result.data:
        raw_phone = row["phone"]
        phone = raw_phone[3:] if str(raw_phone).startswith("wa_") else raw_phone
        count = row.get("follow_up_count", 0)

        lock = redis_conn.lock(f"phone-lock:{phone}", timeout=30, blocking_timeout=2)
        acquired = await asyncio.to_thread(lock.acquire, False)
        if not acquired:
            continue

        try:
            from src.state.chat_state import get_user_state, save_user_state, is_at_qr_stage
            paid_state = get_user_state(phone)
            if paid_state.get("payment_status") == "PAID" or paid_state.get("stage") == "ENROLLED":
                paid_state["follow_up_count"] = 0
                paid_state["next_followup_due_at"] = None
                paid_state["next_follow_up_due_at"] = None
                save_user_state(phone, paid_state)
                continue

            lead_at_qr = is_at_qr_stage(paid_state)
            if lead_at_qr and count >= 1:
                # Leads at QR stage only get the initial reminder; lead stays with owner and AI remains active.
                print(f"[whatsapp_followup] {phone}: QR lead reached count {count} — skipping, AI remains active.")
                await asyncio.to_thread(
                    lambda: supabase.table("user_session_state").update({
                        "next_followup_due_at": None,
                        "is_escalated": False,
                    }).eq("phone", phone).execute()
                )
                continue

            if count < TOTAL_STEPS:
                from src.state.chat_history import get_recent_history
                state = get_user_state(phone)
                course_id = (state.get("course") or "").strip()
                if not course_id:
                    hist = get_recent_history(phone, limit=6)
                    if hist:
                        hist_text = " ".join((t.get("content") or "") for t in hist)
                        course_id = kb_loader.detect_course_from_text(hist_text, default_course=None)

                followup_data = get_followup_for_step(course_id, count)
                if not followup_data or not followup_data.get("message"):
                    print(f"[whatsapp_followup] No followup message found for {phone} (course={course_id}, step={count})")
                    continue

                reply = followup_data["message"]
                res = await send_text_message_async(phone, reply)
                is_failed = isinstance(res, dict) and "error" in res

                if is_failed:
                    print(f"[whatsapp_followup] ⚠️ Send failed for {phone}: {res.get('error')}")
                else:
                    await save_message_async(phone, "assistant", reply)
                    await log_message_async(phone, "ai", reply)

                    next_gap = get_next_gap_seconds(count)
                    next_count = count + 1
                    next_due_iso = datetime.fromtimestamp(now_ts + next_gap, tz=timezone.utc).isoformat() if next_gap else None

                    await asyncio.to_thread(
                        lambda: supabase.table("user_session_state").update({
                            "follow_up_count": next_count,
                            "next_followup_due_at": next_due_iso,
                        }).eq("phone", phone).execute()
                    )
                    try:
                        st = get_user_state(phone)
                        st["follow_up_count"] = next_count
                        st["next_followup_due_at"] = (now_ts + next_gap) if next_gap else None
                        save_user_state(phone, st)
                    except Exception:
                        pass

                    delay_lbl = followup_data.get("delay_label", f"step {count}")
                    is_trial = followup_data.get("is_final_trial_offer")
                    if is_trial:
                        print(f"[whatsapp_followup] {phone}: 🎁 Sent Step {count} ({delay_lbl}) [3 FREE TRIALS OFFER] — next agent handoff in {next_gap}s")
                    else:
                        print(f"[whatsapp_followup] {phone}: Sent Step {count} ({delay_lbl}) — next due in {next_gap}s")

            elif count >= TOTAL_STEPS:
                # 24 hours completed without reply after the 3 Free Trials offer -> Escalate to human agent in Interakt
                agent, agent_index = get_next_agent_email()
                reply = "Connecting you with our team now. Someone will be with you shortly! 🙏"
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
                        "follow_up_count": TOTAL_STEPS + 1,
                        "next_followup_due_at": None,
                        "is_escalated": True,
                    }).eq("phone", phone).execute()
                )
                try:
                    st = get_user_state(phone)
                    st["follow_up_count"] = TOTAL_STEPS + 1
                    st["next_followup_due_at"] = None
                    st["is_escalated"] = True
                    save_user_state(phone, st)
                except Exception:
                    pass
                print(f"[whatsapp_followup] {phone}: 24-hour cycle completed — escalated to human agent ({agent})")

        except Exception as e:
            print(f"[whatsapp_followup] Error processing {phone}: {e}")
        finally:
            try:
                await asyncio.to_thread(lock.release)
            except Exception:
                pass

    print(f"[whatsapp_followup] Sweep finished at {time.strftime('%H:%M:%S')}")


async def main_loop():
    print(f"[whatsapp_followup] Started — polling every {POLL_INTERVAL}s")
    cycle = 0
    while True:
        cycle += 1
        print(f"\n[whatsapp_followup] --- Cycle #{cycle} ---")
        await sweep_once()
        print(f"[whatsapp_followup] Sleeping {POLL_INTERVAL}s until next cycle...\n")
        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main_loop())
