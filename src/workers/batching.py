"""
batching.py — implements fast, reliable debouncing (1.2s idle gap, 2.5s max burst cap).
If another message from the SAME person arrives during the window,
combines them into a single prompt.

Uses an asyncio task debouncer + Redis token verification:
- Single message users wait only 1.2s (faster response).
- Rapid burst users wait max 2.5s total from 1st message.
- 100% reliable execution after user finishes typing.
"""

import os
import uuid
import json
import time
import asyncio
from dotenv import load_dotenv
from src.services.redis_client import get_redis_connection, get_async_redis_connection
from src.workers.tasks import process_incoming_message_async

load_dotenv()

IDLE_DEBOUNCE_SECONDS = float(os.getenv("IDLE_DEBOUNCE_SECONDS", "4.0"))   # 4.0s idle gap wait per message
MAX_BURST_CAP_SECONDS = float(os.getenv("MAX_BURST_CAP_SECONDS", "8.0"))   # 8.0s max burst window cap

_active_debounce_tasks = {}


async def _async_debounce_timer(phone: str, token: str, wait_seconds: float):
    """
    Waits wait_seconds. If no newer message arrived for this phone number,
    pulls all messages from batch:{phone}, combines them, and processes the AI reply.
    """
    await asyncio.sleep(wait_seconds)
    r = get_async_redis_connection()
    trigger_key = f"batch_trigger:{phone}"
    referral_key = f"batch_referral:{phone}"
    first_time_key = f"batch_first_time:{phone}"

    try:
        current_token = await r.get(trigger_key)
        current_token_str = current_token.decode() if isinstance(current_token, bytes) else current_token

        if not current_token_str or current_token_str != token:
            # A newer message arrived during the wait window — let newer job handle it
            return

        batch_key = f"batch:{phone}"
        raw_messages = await r.lrange(batch_key, 0, -1)
        if not raw_messages:
            return

        messages = [m.decode() if isinstance(m, bytes) else m for m in raw_messages]
        combined_text = "\n".join(messages)

        # Retrieve referral if any
        raw_referral = await r.get(referral_key)
        referral = None
        if raw_referral:
            try:
                referral = json.loads(raw_referral.decode() if isinstance(raw_referral, bytes) else raw_referral)
            except Exception:
                pass

        # Clear batch keys asynchronously
        await r.delete(batch_key, trigger_key, referral_key, first_time_key)

        # Process asynchronously
        await process_incoming_message_async(phone, combined_text, referral=referral)

    except Exception as e:
        print(f"[batching] Error in async debounce timer for {phone}: {e}")
        # Fallback: try to process whatever is in the batch
        try:
            batch_key = f"batch:{phone}"
            raw_messages = await r.lrange(batch_key, 0, -1)
            if raw_messages:
                messages = [m.decode() if isinstance(m, bytes) else m for m in raw_messages]
                combined_text = "\n".join(messages)

                raw_referral = await r.get(referral_key)
                referral = None
                if raw_referral:
                    try:
                        referral = json.loads(raw_referral.decode() if isinstance(raw_referral, bytes) else raw_referral)
                    except Exception:
                        pass

                await r.delete(batch_key, trigger_key, referral_key, first_time_key)
                await process_incoming_message_async(phone, combined_text, referral=referral)
        except Exception as ex:
            print(f"[batching] Fallback processing error for {phone}: {ex}")


async def add_message_to_batch_async(phone: str, text: str, referral: dict = None):
    """
    Async function to add a message to this phone's batch
    and spawn the debounce timer task with 1.2s idle gap and 2.5s max cap.
    Fully non-blocking using async Redis.
    """
    r = get_async_redis_connection()
    batch_key = f"batch:{phone}"
    trigger_key = f"batch_trigger:{phone}"
    referral_key = f"batch_referral:{phone}"
    first_time_key = f"batch_first_time:{phone}"

    await r.rpush(batch_key, text)

    # Save referral data if provided
    if referral:
        await r.setex(referral_key, 60, json.dumps(referral))

    now = time.time()
    first_time_raw = await r.get(first_time_key)
    if not first_time_raw:
        await r.setex(first_time_key, 60, str(now))
        first_time = now
    else:
        try:
            first_time = float(first_time_raw.decode() if isinstance(first_time_raw, bytes) else first_time_raw)
        except Exception:
            first_time = now

    elapsed = now - first_time
    remaining_burst_cap = max(0.1, MAX_BURST_CAP_SECONDS - elapsed)
    wait_seconds = min(IDLE_DEBOUNCE_SECONDS, remaining_burst_cap)

    token = str(uuid.uuid4())
    await r.set(trigger_key, token)

    # Cancel previous debounce task for this phone if still pending
    old_task = _active_debounce_tasks.get(phone)
    if old_task and not old_task.done():
        old_task.cancel()

    task = asyncio.create_task(_async_debounce_timer(phone, token, wait_seconds))
    _active_debounce_tasks[phone] = task




