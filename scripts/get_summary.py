#!/usr/bin/env python3
"""
get_summary.py — Standalone CLI script to inspect client session summary.

Usage:
    python get_summary.py <phone_number>
    python get_summary.py
"""

import os
import sys
import asyncio
from dotenv import load_dotenv

# Ensure UTF-8 output in Windows terminal
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Ensure project root is in sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

load_dotenv(os.path.join(BASE_DIR, ".env"))

from src.core.helpers.client_summary import get_client_summary_async


async def main():
    if len(sys.argv) > 1:
        phone = sys.argv[1].strip()
    else:
        phone = input("Enter client phone number: ").strip()

    if not phone:
        print("❌ Error: Phone number cannot be empty.")
        sys.exit(1)

    print(f"\n🔍 Fetching real-time summary for: {phone}\n")
    summary = await get_client_summary_async(phone)
    print(summary)
    print()


if __name__ == "__main__":
    asyncio.run(main())
