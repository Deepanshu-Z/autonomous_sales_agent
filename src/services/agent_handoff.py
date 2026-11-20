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
import json
import asyncio
from pathlib import Path
from dotenv import load_dotenv

from src.services.interakt import send_text_message_async, assign_chat_to_agent_async
from src.state.chat_state import mark_escalated_async, get_user_state_async, save_user_state_async
from src.state.chat_history import save_message_async
from src.state.csv_logger import log_message_async
from src.services.redis_client import get_redis_connection

load_dotenv()

redis_conn = get_redis_connection()

AGENTS_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "agents.json"

def load_agents() -> list:
    """Loads active agents from data/agents.json with fallback to .env."""
    if AGENTS_FILE.exists():
        try:
            with open(AGENTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                active_agents = [a for a in data if a.get("active", True)]
                if active_agents:
                    return active_agents
        except Exception as e:
            print(f"[agent_handoff] Failed to load {AGENTS_FILE}: {e}")

    # Fallback to .env if file missing or empty
    env_agents = []
    for k in ["PRIORITY_AGENT_EMAIL", "PRIORITY_AGENT_EMAIL_ANOTHER_1", "PRIORITY_AGENT_EMAIL_ANOTHER_2"]:
        val = os.getenv(k)
        if val:
            env_agents.append({"email": val, "name": val.split("@")[0], "phone": "", "active": True})
    return env_agents

ALL_AGENTS = load_agents()
AGENT_POOL = [a["email"] for a in ALL_AGENTS if a.get("email")]
OWNER_EMAIL = os.getenv("OWNER_EMAIL") or os.getenv("PRIORITY_AGENT_EMAIL", "harshanand@sensationzmedia.com")
PRIORITY_AGENT_EMAIL = OWNER_EMAIL


def get_next_agent() -> tuple:
    """
    Returns (agent_dict, agent_index) in round-robin order.
    Uses Redis INCR for atomicity across concurrent requests and processes.
    """
    global ALL_AGENTS
    if not ALL_AGENTS:
        ALL_AGENTS = load_agents()
    if not ALL_AGENTS:
        fallback_email = OWNER_EMAIL
        return {"email": fallback_email, "phone": "", "name": "Default Agent"}, 0

    try:
        counter = redis_conn.incr("agent_round_robin_counter")
    except Exception as e:
        print(f"[agent_handoff] Redis incr failed, using in-memory fallback: {e}")
        counter = getattr(get_next_agent, "_mem_counter", 0) + 1
        setattr(get_next_agent, "_mem_counter", counter)
    index = (counter - 1) % len(ALL_AGENTS)
    agent = ALL_AGENTS[index]
    print(f"[agent_handoff] counter={counter} -> agent[{index}] = {agent.get('name')} ({agent.get('email')})")
    return agent, index


def get_next_agent_email() -> tuple:
    """
    Returns (agent_email, agent_index) in round-robin order.
    Uses Redis INCR for atomicity across concurrent requests and processes.
    """
    agent, index = get_next_agent()
    return agent.get("email"), index


async def execute_agent_handoff_async(
    phone: str,
    user_reply_text: str,
    escalation_reason: str,
    start_time: float = None,
    send_connecting_notice: bool = True,
) -> bool:
    """
    Executes the complete agent handoff pipeline:
    1. Sends the customer-facing message informing them that their query has been heard and an agent is assigned.
    2. Assigns the chat to a sales agent in round-robin order via Interakt API (re-assigning from supreme owner to sales agent).
    3. Marks the user state as escalated in Supabase and cache.
    4. Customer notification message delivery.
    5. Asynchronously logs the interaction to Supabase and CSV.
    """
    print(f"[agent_handoff] Escalating {phone} — Reason: {escalation_reason}")

    state = await get_user_state_async(phone)
    from src.state.chat_state import is_protected_from_agent_assignment
    from src.core.kb_loader import get_support_executive_for_phone
    from src.core.lead_logger import log_lead_action

    is_protected, stage_type = is_protected_from_agent_assignment(state)
    if is_protected:
        print(f"[agent_handoff] 🛡️ Agent assignment STRICTLY PROHIBITED for {phone} ({stage_type}). Routing to customer support executive.")
        exec_info = get_support_executive_for_phone(phone)
        exec_name = str(exec_info.get("name") or "Customer Support").strip()
        sal = str(exec_info.get("salutation") or "").strip()
        display_exec = f"{exec_name} {sal}".strip() if sal and sal.lower() not in exec_name.lower() else exec_name
        exec_phone = str(exec_info.get("phone") or "+91 88260 57446").strip()

        lang = str((state or {}).get("language") or "").lower()
        is_pure_english = "english" in lang and "hinglish" not in lang

        if stage_type == "PAID":
            if is_pure_english:
                support_reply = (
                    "Your classes are already confirmed! 🎉 For any class onboarding, schedule, or technical assistance, "
                    f"you can directly connect with {display_exec} from our customer support team:\n"
                    f"📞 {exec_phone}\n\n"
                    "We are always here to help you! 😊"
                )
            else:
                support_reply = (
                    "Aapki classes already confirmed hain! 🎉 Kisi bhi class onboarding, schedule ya assistance ke liye "
                    f"aap hamari customer support team se directly connect kar sakte hain:\n"
                    f"📞 {display_exec}: {exec_phone}\n\n"
                    "Hum aapki poori madad karenge! 😊"
                )
        else:  # QR_SENT
            if is_pure_english:
                support_reply = (
                    "If you have any questions regarding your batch timing, classes, or payment, you can connect directly "
                    f"with {display_exec} from our customer support team:\n"
                    f"📞 {exec_phone}\n\n"
                    "You can also complete your payment via the UPI QR code sent above to immediately confirm your seat! 😊"
                )
            else:
                support_reply = (
                    "Agar aapko class timing, batch ya payment se related koi bhi sawaal hai, toh aap hamari customer support team "
                    f"se directly connect kar sakte hain:\n"
                    f"📞 {display_exec}: {exec_phone}\n\n"
                    "Aap upar diye gaye UPI QR code se payment complete karke bhi apni seat instantly confirm kar sakte hain! 😊"
                )

        # Deliver message to customer on WhatsApp
        await send_text_message_async(phone, support_reply)
        latency_sec = round(time.time() - start_time, 2) if start_time else None
        asyncio.create_task(save_message_async(phone, "assistant", support_reply, response_time_sec=latency_sec))
        asyncio.create_task(log_message_async(phone, "support_contact", support_reply))

        # Guarantee AI remains un-escalated and active
        try:
            if not state:
                state = await get_user_state_async(phone)
            state["is_escalated"] = False
            state["support_executive_provided"] = display_exec
            state["support_executive_phone"] = exec_phone
            await save_user_state_async(phone, state)
        except Exception as exc:
            print(f"[agent_handoff] Non-fatal state update error for protected lead {phone}: {exc}")

        log_lead_action(
            lead_no=phone,
            lead_details=f"Protected ({stage_type})",
            action_taken="SUPPORT_PROVIDED",
            reason=f"Agent assignment blocked for {stage_type}. Support contact given: {display_exec} ({exec_phone})",
        )
        return True

    # Check if already assigned a human sales agent previously
    assigned_email = state.get("assigned_agent_email") if state else None

    # If currently assigned to supreme owner (monitoring), or not yet assigned,
    # assign to next sales agent in round-robin sequence.
    if assigned_email and assigned_email.strip().lower() != OWNER_EMAIL.strip().lower():
        agent = assigned_email
        agent_index = 0
    else:
        agent, agent_index = get_next_agent_email()

    # 2. Mark state escalated first to prevent race conditions
    await mark_escalated_async(phone)
    try:
        if not state:
            state = await get_user_state_async(phone)
        state["is_escalated"] = True
        state["escalation_reason"] = escalation_reason
        if agent:
            state["assigned_agent_email"] = agent
            state["already_assigned"] = True
        await save_user_state_async(phone, state)
    except Exception as e:
        print(f"[agent_handoff] Error updating state for {phone}: {e}")

    HANDOFF_CONNECTING_MESSAGE = (
        "Connecting you with one of our team members now! 🙏\n\n"
        "Aapki request humari team tak pahunch gayi hai. Someone from our team will be with you shortly 😊"
    )

    # 3. Deliver customer notification message
    if user_reply_text and user_reply_text.strip():
        await send_text_message_async(phone, user_reply_text.strip())
        latency_sec = round(time.time() - start_time, 2) if start_time else None
        asyncio.create_task(save_message_async(phone, "assistant", user_reply_text.strip(), response_time_sec=latency_sec))
        asyncio.create_task(log_message_async(phone, "agent_handoff", user_reply_text.strip()))

        # If requested and the reply didn't already inform them of team contact, send the connecting notice
        if send_connecting_notice:
            reply_lower = user_reply_text.lower()
            already_has_handoff_notice = any(
                phrase in reply_lower
                for phrase in [
                    "team member", "support team", "connecting you",
                    "connect karenge", "someone will be with you", "reach out to you",
                    "support coordinator", "coordinator"
                ]
            )
            if not already_has_handoff_notice:
                await asyncio.sleep(0.5)
                await send_text_message_async(phone, HANDOFF_CONNECTING_MESSAGE)
                asyncio.create_task(save_message_async(phone, "assistant", HANDOFF_CONNECTING_MESSAGE))
                asyncio.create_task(log_message_async(phone, "agent_handoff", HANDOFF_CONNECTING_MESSAGE))
    elif send_connecting_notice:
        await send_text_message_async(phone, HANDOFF_CONNECTING_MESSAGE)
        latency_sec = round(time.time() - start_time, 2) if start_time else None
        asyncio.create_task(save_message_async(phone, "assistant", HANDOFF_CONNECTING_MESSAGE, response_time_sec=latency_sec))
        asyncio.create_task(log_message_async(phone, "agent_handoff", HANDOFF_CONNECTING_MESSAGE))

    # 4. Assign chat in Interakt (native Interakt WhatsApp leads only)
    is_interakt = not (phone.startswith("wa_") or phone.startswith("ig_") or phone.startswith("fb_"))
    if agent and is_interakt:
        assignment_success = await assign_chat_to_agent_async(phone, agent)
        print(f"[agent_handoff] Interakt assignment to {agent}: {assignment_success}")

        # Summary sending to agents has been disabled as requested:
        # from src.services.agent_summary import send_agent_summary_async
        # asyncio.create_task(
        #     send_agent_summary_async(phone, agent_index, escalation_reason=escalation_reason)
        # )

    return True
