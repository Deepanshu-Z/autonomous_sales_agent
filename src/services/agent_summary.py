"""
agent_summary.py — Generates a concise Hinglish conversation summary and notifies agents on handoff.
"""

import os
import asyncio
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from src.services.interakt import send_text_message_async
from src.state.chat_history import get_recent_history
from src.state.chat_state import get_user_state

load_dotenv()

AGENT_NUMBER_POOL = [
    e for e in [
        os.getenv("PRIORITY_AGENT_NUMBER_ANOTHER_1"),
        os.getenv("PRIORITY_AGENT_NUMBER_ANOTHER_2"),
    ] if e
]

_llm = ChatOpenAI(
    model=os.getenv("OPENAI_MODEL", "gpt-4o"),
    api_key=os.getenv("OPENAI_API_KEY"),
    temperature=0.3,
    timeout=20,
    max_retries=1,
)

_SUMMARY_SYSTEM = """Tu ek helpful assistant hai jo Sensationz ke agent ko customer handoff summary bhejta hai.

Tujhe last kuch messages dekh ke ek SHORT summary banana hai -- sirf 3-4 bullet points mein, Hinglish mein.

Summary mein yeh include kar:
- Customer ka main concern / sawaal kya tha
- Enrollment mein kahan tak pahunche (timing/package select hua ya nahi)
- Customer ka mood/interest level (interested / confused / disinterested)
- Agent ko kya karna chahiye (next step / follow-up action)

Format:
*Customer Summary*

- [point 1]
- [point 2]
- [point 3]
- [point 4]

Rules:
- Sirf Hinglish mein likho (Hindi + English mix)
- Bilkul short rakho -- agent busy hai
- Phone number mat likho
- Koi extra explanation mat do, seedha bullet points
"""


async def _generate_summary_llm(history: list, state: dict, escalation_reason: str) -> str:
    """Uses LLM to generate a 3-4 point Hinglish summary of the conversation."""
    chat_lines = []
    for turn in history[-10:]:
        role = "Customer" if turn.get("role") == "user" else "Bot"
        content = (turn.get("content") or "")[:300]
        chat_lines.append(f"{role}: {content}")
    chat_text = "\n".join(chat_lines) if chat_lines else "No conversation history."

    stage = state.get("stage", "NEW")
    timing = state.get("timing") or "Not selected"
    package = state.get("package") or "Not selected"
    fee = state.get("fee") or "N/A"
    app_installed = "Yes" if state.get("app_installed") else "No"
    profile_created = "Yes" if state.get("profile_created") else "No"

    user_prompt = f"""Escalation reason: {escalation_reason}

Funnel Stage: {stage}
Timing Selected: {timing}
Package Selected: {package} ({fee})
App Installed: {app_installed}
Profile Created: {profile_created}

Recent Conversation:
{chat_text}

Ab 3-4 bullet points mein Hinglish summary do."""

    try:
        response = await _llm.ainvoke([
            SystemMessage(content=_SUMMARY_SYSTEM),
            HumanMessage(content=user_prompt),
        ])
        return response.content.strip()
    except Exception as e:
        print(f"[agent_summary] LLM summary failed: {e}")
        return (
            f"*Customer Summary*\n\n"
            f"- Funnel Stage: {stage}\n"
            f"- Timing: {timing} | Package: {package} ({fee})\n"
            f"- App Installed: {app_installed} | Profile: {profile_created}\n"
            f"- Escalation reason: {escalation_reason}"
        )


async def send_agent_summary_async(
    customer_phone: str,
    agent_index: int,
    escalation_reason: str = "User requested agent",
):
    """
    Generates a Hinglish summary of the customer conversation
    and sends it to the matching agent WhatsApp number.

    NOTE: Summary sending to agents has been disabled as requested.
    All dispatch logic is commented out below.
    """
    print(f"[agent_summary] Summary sending disabled -- skipping summary dispatch for {customer_phone}")
    return

    # --- Commented out dispatch logic ---
    # if not AGENT_NUMBER_POOL:
    #     print("[agent_summary] No agent numbers configured -- skipping summary")
    #     return
    #
    # idx = agent_index % len(AGENT_NUMBER_POOL)
    # agent_number = AGENT_NUMBER_POOL[idx]
    #
    # try:
    #     history = get_recent_history(customer_phone, limit=10)
    #     state = get_user_state(customer_phone)
    # except Exception as e:
    #     print(f"[agent_summary] Failed to fetch history/state: {e}")
    #     history = []
    #     state = {}
    #
    # summary = await _generate_summary_llm(history, state, escalation_reason)
    #
    # message = (
    #     f"*New Customer Assigned*\n"
    #     f"Customer: +{customer_phone}\n\n"
    #     f"{summary}\n\n"
    #     f"Interakt mein open karo aur reply karo."
    # )
    # print(f"[agent_summary] Sending summary to agent {agent_number} (index={idx})")
    # try:
    #     await send_text_message_async(agent_number, message)
    # except Exception as e:
    #     print(f"[agent_summary] Failed to send summary to agent: {e}")

