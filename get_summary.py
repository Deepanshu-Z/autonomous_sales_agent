#!/usr/bin/env python3
"""
get_summary.py — Standalone CLI script to inspect client session summary.
Redirects to scripts/get_summary.py.
"""
import os
import sys

scripts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts")
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

from get_summary import main
import asyncio

if __name__ == "__main__":
    asyncio.run(main())
