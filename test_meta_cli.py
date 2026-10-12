"""
test_meta_cli.py — Interactive CLI to test the full Instagram & Facebook flow with any Test ID.

Usage:
    python test_meta_cli.py

Allows you to:
1. Simulate an Instagram Comment from any username/test ID on a post (generates public reply + private DM).
2. Chat back-and-forth via Direct Message (DM) as that same test ID with full conversation memory.
3. Test human agent escalation and session reset.
"""

import sys
import os
import asyncio
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
load_dotenv()

from src.core import meta_loader
from src.api.meta_routes import _handle_comment_async
from src.workers.tasks import process_incoming_message_async
from src.state.chat_state import get_user_state_async, reset_user_state_async, is_escalated_async
from src.state.chat_history import get_recent_history_async


async def run_meta_tester():
    print("=" * 70)
    print("📸 SENSATIONZ INSTAGRAM & FACEBOOK AUTOMATION — INTERACTIVE TESTER 📸")
    print("=" * 70)

    # Let user specify their test handle
    default_handle = "test_user_priya"
    test_username = input(f"Enter test Instagram username (default: {default_handle}): ").strip() or default_handle
    test_sender_id = f"sim_{test_username}"
    user_key = f"ig_{test_sender_id}"

    # Target post
    target_post = meta_loader.get_post_config("TARGET_POST_ID_HERE")
    if not target_post:
        target_post = {
            "post_id": "TARGET_POST_ID_HERE",
            "course": "face_yoga",
            "is_active": True,
            "trigger_keywords": [],
            "public_replies": ["Hey @{username}! Sent details to your DM 📩"],
            "private_dm_intro": "Hi! Welcome to Face Yoga. Would you like morning or evening timings?"
        }

    print(f"\n[Test Profile Initialized]")
    print(f"  • Username   : @{test_username}")
    print(f"  • User Key   : {user_key}")
    print(f"  • Target Post: {target_post.get('post_id')} (Course: {target_post.get('course')})")
    print("-" * 70)
    print("Commands:")
    print("  • /comment <text>  -> Simulate posting a comment on the Instagram post")
    print("  • <text>           -> Send a Direct Message (DM) to continue the chat")
    print("  • /history         -> View conversation history & context")
    print("  • /state           -> View current user state")
    print("  • reset            -> Reset session and clear escalation")
    print("  • exit / quit      -> Exit tester")
    print("=" * 70 + "\n")

    while True:
        try:
            line = input(f"[@{test_username}] > ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not line:
            continue
        if line.lower() in ("exit", "quit", "q"):
            print("Exiting Meta CLI.")
            break

        if line.startswith("/comment"):
            comment_text = line[8:].strip()
            if not comment_text:
                print("Usage: /comment What are the fees?")
                continue
            print(f"\n💬 [Simulating Comment on Post]: '{comment_text}'...")
            await _handle_comment_async(
                post_cfg=target_post,
                comment_id=f"comm_{int(asyncio.get_event_loop().time())}",
                sender_id=test_sender_id,
                username=test_username,
                post_id=target_post.get("post_id"),
                comment_text=comment_text,
                channel="instagram",
            )
            # Show updated history
            history = await get_recent_history_async(user_key)
            print("\n📬 Latest thread messages in DM:")
            for m in history[-2:]:
                print(f"   [{m['role'].upper()}]: {m['content']}")
            print()

        elif line.lower() == "/history":
            history = await get_recent_history_async(user_key)
            print(f"\n📜 Chat History for {user_key} ({len(history)} messages):")
            for i, m in enumerate(history, 1):
                print(f"   {i}. [{m['role'].upper()}]: {m['content']}")
            print()

        elif line.lower() == "/state":
            st = await get_user_state_async(user_key)
            esc = await is_escalated_async(user_key)
            print(f"\n⚙️ State for {user_key}:")
            print(f"   Escalated: {esc}")
            print(f"   Data: {st}\n")

        else:
            # Regular message = Direct Message (DM)
            print(f"\n📩 [Sending DM]: '{line}'...")
            await process_incoming_message_async(user_key, line)
            history = await get_recent_history_async(user_key)
            if history:
                latest_ai = [m for m in history if m["role"] in ("assistant", "ai")]
                if latest_ai:
                    print(f"\n🤖 [AI Reply via DM]:\n{latest_ai[-1]['content']}\n")
                else:
                    print("(No AI reply generated — check escalation or intent)")
            print()


if __name__ == "__main__":
    asyncio.run(run_meta_tester())
