import sys
import os

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

# Auto-switch to virtual environment if running directly with unactivated global Python
_base_dir = os.path.dirname(os.path.abspath(__file__))
_venv_python_win = os.path.join(_base_dir, "venv", "Scripts", "python.exe")
_venv_python_unix = os.path.join(_base_dir, "venv", "bin", "python")
_venv_python = _venv_python_win if os.path.exists(_venv_python_win) else (_venv_python_unix if os.path.exists(_venv_python_unix) else None)

if _venv_python and os.path.abspath(sys.executable).lower() != os.path.abspath(_venv_python).lower() and __name__ == "__main__":
    import subprocess
    sys.exit(subprocess.call([_venv_python, os.path.abspath(__file__)] + sys.argv[1:]))

import uvicorn
from dotenv import load_dotenv

load_dotenv()

def check_env_vars():
    """Verify critical environment variables are set before starting."""
    required = [
        "OPENAI_API_KEY",
        "SUPABASE_URL",
        "SUPABASE_KEY",
        "INTERAKT_API_KEY",
    ]

    has_redis = os.getenv("REDIS_URL") or (os.getenv("UPSTASH_REDIS_REST_URL") and os.getenv("UPSTASH_REDIS_REST_TOKEN"))
    if not has_redis:
        required.append("REDIS_URL (or UPSTASH_REDIS_REST_URL + UPSTASH_REDIS_REST_TOKEN)")

    optional = [
        "REDIS_URL",
        "UPSTASH_REDIS_REST_URL",
        "UPSTASH_REDIS_REST_TOKEN",
        "PHONE_NUMBER_ID",
        "WHATSAPP_TOKEN",
        "QDRANT_URL",
        "QDRANT_API_KEY",
        "PRIORITY_AGENT_EMAIL",
        "OWNER_EMAIL",
        "TARGET_AD_ID",
        "TARGET_MESSAGE_TEXT",
        "RAZORPAY_KEY_ID",
        "RAZORPAY_KEY_SECRET",
        "RAZORPAY_WEBHOOK_SECRET",
    ]

    print("=" * 60)
    print("🚀 WhatsApp Bot — Startup Health Check")
    print("=" * 60)

    missing = []
    for var in required:
        val = os.getenv(var)
        if not val:
            missing.append(var)
            print(f"  ❌ {var}: MISSING (required)")
        else:
            print(f"  ✅ {var}: set ({len(val)} chars)")

    for var in optional:
        val = os.getenv(var)
        if val:
            # Truncate display for safety
            display = val[:20] + "..." if len(val) > 20 else val
            print(f"  ✅ {var}: {display}")
        else:
            print(f"  ⚠️  {var}: not set (optional)")

    if missing:
        print(f"\n❌ FATAL: {len(missing)} required env vars missing: {', '.join(missing)}")
        print("   Fix your .env file and restart. See .env.example for template.")
        sys.exit(1)

    print("=" * 60)
    print("✅ All required environment variables are set.")
    print("=" * 60)


if __name__ == "__main__":
    check_env_vars()

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    host = sys.argv[2] if len(sys.argv) > 2 else "0.0.0.0"

    print(f"\n🌐 Starting uvicorn on {host}:{port}")
    print(f"   Webhook URL:         http://{host}:{port}/webhook")
    print(f"   Razorpay Webhook:    http://{host}:{port}/api/razorpay/webhook")
    print(f"   Interakt Test URL:   http://{host}:{port}/test-webhook")
    print(f"   WhatsApp Cloud URL:  http://{host}:{port}/test-whatsapp-webhook\n")

    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        workers=1,  # Single worker — in-memory state requires it
        log_level="info",
    )

