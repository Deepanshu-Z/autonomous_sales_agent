"""
agent_handoff.py — Reusable Agent Assignment & Escalation Service.

Centralizes the human agent handoff logic across all components:
- Round-robin agent selection (atomic via Redis INCR)
- Interakt agent chat assignment
- System state escalation marking
- Customer notification message delivery
- Automated agent summary generation & WhatsApp dispatch
- Async non-blocking logging
"""

import os
import time
import asyncio
from dotenv import load_dotenv

from src.services.interakt import send_text_message_async, assign_chat_to_agent_async
from src.state.chat_state import mark_escalated_async, get_user_state_async, save_user_state_async
from src.state.chat_history import save_message_async
from src.state.csv_logger import log_message_async
from src.services.redis_client import get_redis_connection

load_dotenv()

redis_conn = get_redis_connection()

PRIORITY_AGENT_EMAIL = os.getenv("PRIORITY_AGENT_EMAIL")
PRIORITY_AGENT_EMAIL_ANOTHER_1 = os.getenv("PRIORITY_AGENT_EMAIL_ANOTHER_1")
PRIORITY_AGENT_EMAIL_ANOTHER_2 = os.getenv("PRIORITY_AGENT_EMAIL_ANOTHER_2")

AGENT_POOL = [e for e in [PRIORITY_AGENT_EMAIL_ANOTHER_1, PRIORITY_AGENT_EMAIL_ANOTHER_2] if e]


def get_next_agent_email() -> tuple:
    """
    Returns (agent_email, agent_index) in round-robin order.
    Uses Redis INCR for atomicity across concurrent requests and processes.
    """
    if not AGENT_POOL:
        return PRIORITY_AGENT_EMAIL, 0
    counter = redis_conn.incr("agent_round_robin_counter")
    index = (counter - 1) % len(AGENT_POOL)
    agent = AGENT_POOL[index]
    print(f"[agent_handoff] counter={counter} -> agent[{index}] = {agent}")
    return agent, index


async def execute_agent_handoff_async(
    phone: str,
    user_reply_text: str,
    escalation_reason: str,
    start_time: float = None,
) -> bool:
    """
    Executes the complete agent handoff pipeline:
    1. Sends the customer-facing message informing them that their query has been heard and an agent is assigned.
    2. Assigns the chat to the next agent in round-robin order via Interakt API.
    3. Marks the user state as escalated in Supabase and cache.
    4. Generates a summary and sends it to the assigned agent's WhatsApp.
    5. Asynchronously logs the interaction to Supabase and CSV.
    """
    print(f"[agent_handoff] Escalating {phone} — Reason: {escalation_reason}")

    # 1. Select agent
    agent, agent_index = get_next_agent_email()

    # 2. Mark state escalated first to prevent race conditions
    await mark_escalated_async(phone)
    try:
        state = await get_user_state_async(phone)
        state["is_escalated"] = True
        state["escalation_reason"] = escalation_reason
        await save_user_state_async(phone, state)
    except Exception as e:
        print(f"[agent_handoff] Error updating state for {phone}: {e}")

    # 3. Deliver customer notification message
    if user_reply_text:
        await send_text_message_async(phone, user_reply_text)

    # 4. Assign chat in Interakt
    if agent:
        assignment_success = await assign_chat_to_agent_async(phone, agent)
        print(f"[agent_handoff] Interakt assignment to {agent}: {assignment_success}")

    # 5. Background logging (non-blocking)
    latency_sec = round(time.time() - start_time, 2) if start_time else None
    if user_reply_text:
        asyncio.create_task(save_message_async(phone, "assistant", user_reply_text, response_time_sec=latency_sec))
        asyncio.create_task(log_message_async(phone, "agent_handoff", user_reply_text))

    return True
