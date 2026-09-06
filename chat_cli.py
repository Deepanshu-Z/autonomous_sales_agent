"""
chat_cli.py — Interactive Terminal Tester for Sensationz AI Assistant.

Run this script to test any question directly in your terminal with real-time streaming,
conversation memory, and state inspection.

Usage:
    python chat_cli.py
"""

import sys
import os
import time
import asyncio
from dotenv import load_dotenv

# Ensure UTF-8 output on Windows terminal for emojis and Hindi script
sys.stdout.reconfigure(encoding="utf-8")
load_dotenv()

from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_openai import ChatOpenAI
from src.core import prompt
from src.core import kb_loader
from src.core.coupons import detect_package_from_text

# Initialize OpenAI LLM
llm = ChatOpenAI(
    model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
    api_key=os.getenv("OPENAI_API_KEY"),
    temperature=0.3,
    timeout=30,
)


def print_banner():
    print("=" * 65)
    print("  🧘‍♀️ SENSATIONZ AI ASSISTANT — INTERACTIVE TERMINAL TESTER 🧘‍♀️")
    print("=" * 65)
    print("Commands:")
    print("  • Type your question and press Enter to chat")
    print("  • 'reset'        -> Clear chat history & start fresh session")
    print("  • 'done'         -> Simulate user completing profile setup (unlocks coupon)")
    print("  • 'state'        -> View current customer session context")
    print("  • 'course <name>'-> Switch active course (e.g. 'course kathak', 'course yoga')")
    print("  • 'exit' or 'q'  -> Quit tester")
    print("=" * 65 + "\n")


async def chat_session():
    print_banner()

    state = {
        "stage": "NEW",
        "timing": None,
        "package": None,
        "fee": None,
        "app_installed": False,
        "profile_created": False,
        "course": "yoga",
    }
    chat_history = []  # list of {"role": "user"|"assistant", "content": "..."}

    while True:
        try:
            user_input = input("\n👤 You: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nExiting. Goodbye!")
            break

        if not user_input:
            continue

        cmd = user_input.lower()

        # Handle Commands
        if cmd in ["exit", "quit", "q"]:
            print("Exiting. Goodbye!")
            break

        if cmd == "reset":
            state = {
                "stage": "NEW",
                "timing": None,
                "package": None,
                "fee": None,
                "app_installed": False,
                "profile_created": False,
                "course": "yoga",
            }
            chat_history = []
            print("🔄 Session reset! Starting a brand new customer conversation.")
            continue

        if cmd == "done":
            state["profile_created"] = True
            state["app_installed"] = True
            state["stage"] = "PROFILE_COMPLETED"
            print("🔓 [SIMULATION] User profile marked as CREATED. Welcome coupon is now UNLOCKED!")
            continue

        if cmd == "state":
            print("\n📊 Current Customer State:")
            for k, v in state.items():
                print(f"   • {k}: {v}")
            continue

        if cmd.startswith("course "):
            course_name = cmd.split(" ", 1)[1].strip()
            state["course"] = course_name
            print(f"🎯 Switched active course to: '{course_name}'")
            continue

        # Check if user indicates profile creation completion (e.g. 'bnadi', 'bna di', 'kardi', 'done', 'profile ban gayi')
        from src.state.chat_state import is_profile_completed_signal
        if is_profile_completed_signal(user_input, state):
            state["profile_created"] = True
            state["app_installed"] = True
            state["stage"] = "PROFILE_COMPLETED"

        # Detect if user explicitly mentions a course from catalog and persist in session state
        detected_course = kb_loader.detect_course_from_text(user_input, default_course=None)
        if detected_course:
            state["course"] = detected_course

        active_course = state.get("course", "yoga")

        # Detect package from text to update state
        pkg = detect_package_from_text(user_input, course_id=active_course)
        if pkg:
            state["package"] = pkg
            valid_pkgs = kb_loader.get_valid_packages(active_course)
            state["fee"] = valid_pkgs.get(pkg, state.get("fee"))

        # Format clean system prompt with state and course JSON
        sys_prompt_content = prompt.format_system_prompt(state, course_id=active_course)

        # Build message chain
        messages = [SystemMessage(content=sys_prompt_content)]

        # Add recent conversation history (last 8 turns)
        for turn in chat_history[-8:]:
            if turn["role"] == "user":
                messages.append(HumanMessage(content=turn["content"]))
            elif turn["role"] == "assistant":
                messages.append(AIMessage(content=turn["content"]))

        # Immediate context user prompt
        user_prompt = (
            f"Customer's Message: \"{user_input}\"\n\n"
            "INSTRUCTION:\n"
            "1. STRICT LANGUAGE MATCHING: Match the language of Customer's Message (English -> 100% English, Hindi -> Hindi, Hinglish -> Hinglish).\n"
            "2. Answer EVERY distinct question or topic thoroughly.\n"
            "3. Follow all formatting rules (*bold* with single asterisks, • bullets, raw links)."
        )
        messages.append(HumanMessage(content=user_prompt))

        print("\n🤖 Sensationz AI: ", end="", flush=True)
        t0 = time.perf_counter()
        full_reply = ""

        try:
            # Stream response in real-time
            resp = await llm.ainvoke(messages)
            full_reply = resp.content.strip()
            print(full_reply)

            latency = time.perf_counter() - t0
            print(f"\n⚡ Latency: {latency:.2f}s | Tokens: ~{len(full_reply.split())} words")

            # Store in chat history
            chat_history.append({"role": "user", "content": user_input})
            chat_history.append({"role": "assistant", "content": full_reply})

        except Exception as e:
            print(f"\n❌ Error generating reply: {e}")


if __name__ == "__main__":
    asyncio.run(chat_session())
