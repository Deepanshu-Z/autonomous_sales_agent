import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_openai import ChatOpenAI
from src.core import prompt, kb_loader
from src.state.chat_state import sync_state_from_llm_reply

llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.3)

state = {
    "stage": "NEW",
    "timing": None,
    "package": None,
    "fee": None,
    "app_installed": False,
    "profile_created": False,
    "course": "yoga"
}
chat_history = []

async def turn(user_input):
    global state, chat_history
    print(f"\n👤 You: {user_input}")

    active_course = state.get("course", "yoga")
    sys_content = prompt.format_system_prompt(state, course_id=active_course)
    msgs = [SystemMessage(content=sys_content)]
    for t in chat_history[-6:]:
        if t["role"] == "user":
            msgs.append(HumanMessage(content=t["content"]))
        else:
            msgs.append(AIMessage(content=t["content"]))

    last_ai_msg = ""
    for t in reversed(chat_history):
        if t["role"] == "assistant":
            last_ai_msg = t["content"]
            break

    msgs.append(HumanMessage(content=(
        f"--- IMMEDIATE CONTEXT ---\n"
        f"AI's Last Message: \"{last_ai_msg}\"\n"
        f"Customer's Current Reply: \"{user_input}\"\n\n"
        "INSTRUCTION:\n"
        "1. Analyze the customer's reply specifically in context of the AI's last message.\n"
        "2. Follow all welcome coupon and profile rules from the system prompt (if customer created/completed profile or registered, celebrate and include [UNLOCK_COUPON]).\n"
        "3. STRICT LANGUAGE MATCHING: Match the language of Customer's Current Reply.\n"
        "4. Follow WhatsApp formatting (*bold* with single asterisks, • bullets, raw links)."
    )))

    resp = await llm.ainvoke(msgs)
    ans = resp.content.strip()

    # LLM output is the signal
    from src.state.chat_state import resolve_llm_reply_tags
    was_synced, resolved_ans = resolve_llm_reply_tags(ans, state, course_id=active_course)
    print(f"🤖 AI:\n{resolved_ans}\n")
    if was_synced:
        print("  🔓 [State Synced from LLM Output: profile_created=True, coupon_sent=True!]")

    chat_history.append({"role": "user", "content": user_input})
    chat_history.append({"role": "assistant", "content": resolved_ans})

async def main():
    global state, chat_history
    print("\n=== TEST 1: YOGA FLOW (Slang: 'bnadi') ===")
    await turn("coupon bjho")
    assert not state["profile_created"], "Profile should NOT be created on coupon ask!"
    await turn("bnadi")
    assert state["profile_created"], "Profile MUST be created after LLM delivers coupon code!"
    assert state["coupon_sent"], "Coupon sent MUST be True!"

    print("\n=== TEST 2: KATHAK FLOW (Slang: 'maine account bana liya bro') ===")
    state = {
        "stage": "NEW",
        "timing": None,
        "package": None,
        "fee": None,
        "app_installed": False,
        "profile_created": False,
        "course": "kathak"
    }
    chat_history = []
    await turn("kathak ka coupon bjho")
    assert not state["profile_created"], "Profile should NOT be created on coupon ask!"
    await turn("maine account bana liya bro")
    assert state["profile_created"], "Profile MUST be created after LLM delivers coupon code!"
    assert state["coupon_sent"], "Coupon sent MUST be True!"

    print("\n✅ All zero-keyword LLM-driven tests passed successfully!")

if __name__ == "__main__":
    asyncio.run(main())
