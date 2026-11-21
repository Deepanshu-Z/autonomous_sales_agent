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

from typing import Optional

from src.services.interakt import (
    send_text_message_async,
    assign_chat_to_agent_detailed_async,
    ASSIGNED,
    NOT_INTERAKT_CHANNEL,
    AGENT_NOT_FOUND,
    CUSTOMER_NOT_FOUND,
)
from src.state.chat_state import (
    mark_escalated_async,
    clear_escalation_async,
    get_user_state_async,
    save_user_state_async,
)
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


# ── Agent availability (an email Interakt rejected is skipped for a while) ────
AGENT_UNAVAILABLE_TTL_SEC = int(os.getenv("AGENT_UNAVAILABLE_TTL_SEC", str(6 * 3600)))


def _unavailable_key(email: str) -> str:
    return f"agent_unavailable:{email.strip().lower()}"


def mark_agent_unavailable(email: str, reason: str) -> None:
    """Skip this agent in the rotation for AGENT_UNAVAILABLE_TTL_SEC (e.g. Interakt: agent not found)."""
    print(f"[agent_handoff] ❌ ERROR: agent {email} unavailable ({reason}) — skipped for {AGENT_UNAVAILABLE_TTL_SEC // 3600}h. "
          f"Fix or deactivate it in data/agents.json.")
    try:
        redis_conn.setex(_unavailable_key(email), AGENT_UNAVAILABLE_TTL_SEC, reason)
    except Exception as exc:
        print(f"[agent_handoff] Could not mark {email} unavailable in Redis: {exc}")


def is_agent_available(email: Optional[str]) -> bool:
    """True if the agent is active in data/agents.json and not marked unavailable."""
    if not email:
        return False
    if email.strip().lower() not in {e.strip().lower() for e in AGENT_POOL}:
        return False
    try:
        return not redis_conn.exists(_unavailable_key(email))
    except Exception:
        return True  # Redis down: don't block assignments; Interakt will still tell us if it's bad


MAX_AGENT_ATTEMPTS = 2  # agents tried before falling back to the owner (bounds handoff latency)


def _next_available_agent(exclude: set) -> Optional[str]:
    """Next agent in the round-robin that is active, available and not already tried."""
    for _ in range(len(ALL_AGENTS)):
        email, _ = get_next_agent_email()
        if email and email not in exclude and is_agent_available(email):
            return email
    return None


async def assign_lead_to_available_agent_async(phone: str, preferred_agent: Optional[str] = None) -> Optional[str]:
    """
    Assign the chat to someone who actually exists in Interakt; return who, or None if nobody could be.

    Order: preferred agent (only if still active + available) → next agents in the round-robin
    → the owner as last resort. An agent Interakt reports as not found is marked unavailable
    and the next agent is tried immediately. Instagram/Facebook chats have no Interakt
    assignment (their inbox shows every chat): the chosen agent is returned as-is.
    """
    tried: set = set()
    agent = preferred_agent if is_agent_available(preferred_agent) else _next_available_agent(tried)

    for _ in range(MAX_AGENT_ATTEMPTS):
        if not agent:
            break
        tried.add(agent)
        result = await assign_chat_to_agent_detailed_async(phone, agent)
        if result in (ASSIGNED, NOT_INTERAKT_CHANNEL):
            return agent
        if result == CUSTOMER_NOT_FOUND:
            print(f"[agent_handoff] ❌ ERROR: {phone} is not an Interakt customer — cannot assign anyone")
            return None
        if result == AGENT_NOT_FOUND:
            mark_agent_unavailable(agent, "Interakt: agent with email not found")
        agent = _next_available_agent(tried)

    if OWNER_EMAIL not in tried:
        result = await assign_chat_to_agent_detailed_async(phone, OWNER_EMAIL)
        if result in (ASSIGNED, NOT_INTERAKT_CHANNEL):
            print(f"[agent_handoff] ⚠️ {phone}: assigned to OWNER as fallback (agents tried: {sorted(tried)})")
            return OWNER_EMAIL

    print(f"[agent_handoff] ❌ ERROR: {phone}: no agent (incl. owner) accepted the assignment")
    return None


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
    from src.core.lead_logger import log_lead_action

    is_protected, stage_type = is_protected_from_agent_assignment(state)
    if is_protected:
        print(f"[agent_handoff] 🛡️ Agent assignment STRICTLY PROHIBITED for {phone} ({stage_type}). Providing all customer support executives.")
        support_contacts = _support_contacts()

        lang = str((state or {}).get("language") or "").lower()
        is_pure_english = "english" in lang and "hinglish" not in lang

        if stage_type == "PAID":
            if is_pure_english:
                support_reply = (
                    "Your classes are already confirmed! 🎉 For any class onboarding, schedule, or technical assistance, "
                    "you can directly connect with our Customer Support team:\n\n"
                    f"{support_contacts}\n\n"
                    "We are always here to help you! 😊"
                )
            else:
                support_reply = (
                    "Aapki classes already confirmed hain! 🎉 Kisi bhi class onboarding, schedule ya assistance ke liye "
                    "aap hamari Customer Support team se directly connect kar sakte hain:\n\n"
                    f"{support_contacts}\n\n"
                    "Hum aapki poori madad karenge! 😊"
                )
        else:  # QR_SENT
            if is_pure_english:
                support_reply = (
                    "If you have any questions regarding your batch timing, classes, or payment, you can connect directly "
                    "with our Customer Support team:\n\n"
                    f"{support_contacts}\n\n"
                    "You can also complete your payment via the UPI QR code sent above to immediately confirm your seat! 😊"
                )
            else:
                support_reply = (
                    "Agar aapko class timing, batch ya payment se related koi bhi sawaal hai, toh aap hamari Customer Support team "
                    "se directly connect kar sakte hain:\n\n"
                    f"{support_contacts}\n\n"
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
            state["support_executives_provided"] = True
            await save_user_state_async(phone, state)
        except Exception as exc:
            print(f"[agent_handoff] Non-fatal state update error for protected lead {phone}: {exc}")

        log_lead_action(
            lead_no=phone,
            lead_details=f"Protected ({stage_type})",
            action_taken="SUPPORT_PROVIDED",
            reason=f"Agent assignment blocked for {stage_type}. All support contacts given.",
        )
        return True

    # 1. Mark escalated first so the AI can't answer in parallel while we assign.
    await mark_escalated_async(phone)

    # 2. Assign to an agent who really exists BEFORE promising anything to the customer.
    #    A previous sales agent is reused only if still active and available.
    previous_agent = (state or {}).get("assigned_agent_email")
    preferred_agent = (
        previous_agent
        if previous_agent and previous_agent.strip().lower() != OWNER_EMAIL.strip().lower()
        else None
    )
    agent = await assign_lead_to_available_agent_async(phone, preferred_agent=preferred_agent)

    if not agent:
        # Nobody could take the chat: never say "connecting you". Keep the AI active and
        # give the customer the support numbers instead.
        await clear_escalation_async(phone)
        fallback_reply = _handoff_failed_reply(state)
        await send_text_message_async(phone, fallback_reply)
        latency_sec = round(time.time() - start_time, 2) if start_time else None
        asyncio.create_task(save_message_async(phone, "assistant", fallback_reply, response_time_sec=latency_sec))
        asyncio.create_task(log_message_async(phone, "support_contact", fallback_reply))
        log_lead_action(
            lead_no=phone,
            lead_details="Handoff failed",
            action_taken="SUPPORT_PROVIDED",
            reason=f"No agent could be assigned in Interakt. Reason for handoff: {escalation_reason}",
        )
        return False

    # 3. Persist who owns the chat (reload: mark_escalated_async saved a newer copy).
    try:
        state = await get_user_state_async(phone)
        state["is_escalated"] = True
        state["escalation_reason"] = escalation_reason
        state["assigned_agent_email"] = agent
        state["already_assigned"] = True
        state["assigned_at"] = int(time.time())
        await save_user_state_async(phone, state)
    except Exception as e:
        print(f"[agent_handoff] Error updating state for {phone}: {e}")
    print(f"[agent_handoff] ✅ {phone} assigned to {agent}")

    HANDOFF_CONNECTING_MESSAGE = (
        "Connecting you with one of our team members now! 🙏\n\n"
        "Aapki request humari team tak pahunch gayi hai. Someone from our team will be with you shortly 😊"
    )

    # 4. Only now — the chat really has an owner — tell the customer.
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

    return True


_DEFAULT_SUPPORT_CONTACTS = (
    "• Rachna ma'am: +91 88260 57446\n"
    "• Sudesh ma'am: +91 88105 82411\n"
    "• Monika ma'am: +91 93544 05863\n"
    "• Ruchi ma'am: +91 88003 48485"
)


def _support_contacts() -> str:
    from src.core.kb_loader import format_support_executives_display
    return format_support_executives_display() or _DEFAULT_SUPPORT_CONTACTS


def _handoff_failed_reply(state: Optional[dict]) -> str:
    """Sent when no agent could be assigned: honest, with direct support numbers."""
    lang = str((state or {}).get("language") or "").lower()
    if "english" in lang and "hinglish" not in lang:
        return (
            "Our team members are busy right now. 🙏 You can reach our Customer Support team directly:\n\n"
            f"{_support_contacts()}\n\n"
            "Meanwhile, I'm happy to help you here with any question! 😊"
        )
    return (
        "Abhi humari team ke members busy hain. 🙏 Aap hamari Customer Support team se directly baat kar sakte hain:\n\n"
        f"{_support_contacts()}\n\n"
        "Tab tak aapka koi bhi sawaal ho toh main yahin madad karne ke liye hoon! 😊"
    )
