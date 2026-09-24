import sys
import os
import asyncio
sys.path.insert(0, os.path.abspath("."))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from src.state.chat_history import get_recent_history_async

async def main():
    h = await get_recent_history_async("917503736651")
    print(f"Total messages for 917503736651: {len(h)}")
    for m in h[-3:]:
        role = m.get("role")
        content = m.get("content")
        print(f"=== [{role}] ===\n{content}\n")

if __name__ == "__main__":
    asyncio.run(main())
