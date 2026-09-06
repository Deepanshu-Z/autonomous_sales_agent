import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_openai import ChatOpenAI
from src.core import prompt, kb_loader
from src.state.chat_state import is_profile_completed_signal

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
    
    if is_profile_completed_signal(user_input, state):
        state["profile_created"] = True
        state["app_installed"] = True
        state["stage"] = "PROFILE_COMPLETED"
        print("  🔓 [State: profile_created marked as True!]")

    active_course = state.get("course", "yoga")
    sys_content = prompt.format_system_prompt(state, course_id=active_course)
    msgs = [SystemMessage(content=sys_content)]
    for t in chat_history[-6:]:
        if t["role"] == "user":
            msgs.append(HumanMessage(content=t["content"]))
        else:
            msgs.append(AIMessage(content=t["content"]))
            
    msgs.append(HumanMessage(content=(
        f"Customer's Message: \"{user_input}\"\n\n"
        "INSTRUCTION:\n"
        "1. STRICT LANGUAGE MATCHING: Match the language of Customer's Message.\n"
        "2. If user confirmed profile creation (e.g. 'bnadi', 'done') or asks for coupon when unlocked, celebrate and share their welcome discount coupon code with checkout instructions!\n"
        "3. Follow WhatsApp formatting (*bold* with single asterisks, • bullets, raw links)."
    )))
    
    resp = await llm.ainvoke(msgs)
    ans = resp.content.strip()
    print(f"🤖 AI:\n{ans}\n")
    chat_history.append({"role": "user", "content": user_input})
    chat_history.append({"role": "assistant", "content": ans})

async def main():
    print("\n=== TEST 1: YOGA FLOW ===")
    await turn("coupon bjho")
    await turn("bnadi")

    print("\n=== TEST 2: KATHAK FLOW ===")
    global state, chat_history
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
    await turn("bna di")

if __name__ == "__main__":
    asyncio.run(main())
