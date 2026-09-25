"""
run_workers.py — Multi-process worker launcher for production concurrency.

Starts N uvicorn worker processes sharing the same FastAPI app.
Each worker has its own asyncio event loop, so different phone numbers
process truly concurrently across workers.

Usage:
    python run_workers.py 20        # 20 workers, default port 8000
    python run_workers.py 50        # 50 workers
    python run_workers.py 20 8080   # 20 workers on port 8080

Architecture:
    ┌───────────────────────────────────────────┐
    │          uvicorn (N worker processes)      │
    │  ┌────────┐ ┌────────┐       ┌────────┐  │
    │  │Worker 1│ │Worker 2│  ...  │Worker N│  │
    │  │Loop    │ │Loop    │       │Loop    │  │
    │  └───┬────┘ └───┬────┘       └───┬────┘  │
    │      │          │                │        │
    │      └──────────┴────────────────┘        │
    │                  │                         │
    │           Redis (shared state)             │
    │    - batching debounce queues              │
    │    - user session state                    │
    │    - round-robin agent counter             │
    │    - per-phone conversation history        │
    └───────────────────────────────────────────┘

Key design:
  - NOT 20 copies of the app — one uvicorn master with N forked workers
  - All workers share Redis for state (batching, sessions, round-robin)
  - OS load-balances incoming requests across workers
  - Each worker has its own asyncio event loop (true parallelism)
  - Same phone's messages still serialize via the batching debouncer
  - Different phones process on different workers concurrently
"""

import sys
import os
import uvicorn
from dotenv import load_dotenv

load_dotenv()


def check_env_vars():
    """Verify critical environment variables before starting."""
    required = [
        "OPENAI_API_KEY",
        "SUPABASE_URL",
        "SUPABASE_KEY",
        "INTERAKT_API_KEY",
        "UPSTASH_REDIS_REST_URL",
        "UPSTASH_REDIS_REST_TOKEN",
    ]
    optional = [
        "QDRANT_URL",
        "QDRANT_API_KEY",
        "PRIORITY_AGENT_EMAIL",
        "PRIORITY_AGENT_EMAIL_ANOTHER_1",
        "PRIORITY_AGENT_EMAIL_ANOTHER_2",
        "TARGET_AD_ID",
        "TARGET_MESSAGE_TEXT",
    ]

    print("=" * 60)
    print("  WhatsApp Bot — Startup Health Check")
    print("=" * 60)

    missing = []
    for var in required:
        val = os.getenv(var)
        if not val:
            missing.append(var)
            print(f"  [FAIL] {var}: MISSING (required)")
        else:
            print(f"  [ OK ] {var}: set ({len(val)} chars)")

    for var in optional:
        val = os.getenv(var)
        if val:
            display = val[:20] + "..." if len(val) > 20 else val
            print(f"  [ OK ] {var}: {display}")
        else:
            print(f"  [WARN] {var}: not set (optional)")

    if missing:
        print(f"\n  FATAL: {len(missing)} required env vars missing: {', '.join(missing)}")
        print("  Fix your .env file and restart. See .env.example for template.")
        sys.exit(1)

    print("=" * 60)
    print("  All required environment variables are set.")
    print("=" * 60)


if __name__ == "__main__":
    # Parse arguments
    num_workers = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8000
    host = "0.0.0.0"

    check_env_vars()

    print(f"\n  Starting {num_workers} uvicorn worker(s) on {host}:{port}")
    print(f"  Webhook URL:  http://{host}:{port}/webhook")
    print(f"  Test URL:     http://{host}:{port}/test-webhook")
    print(f"  Workers:      {num_workers}")
    print(f"  Architecture: multi-process (each worker = independent event loop)")
    print()

    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        workers=num_workers,
        log_level="info",
        access_log=False,  # reduce noise in load tests
    )
