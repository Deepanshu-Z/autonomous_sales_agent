import sys
import os
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
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
        "UPSTASH_REDIS_REST_URL",
        "UPSTASH_REDIS_REST_TOKEN",
        "PROD_APP_DB_URL",
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
    print(f"   Webhook URL: http://{host}:{port}/webhook")
    print(f"   Test URL:    http://{host}:{port}/test-webhook\n")

    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=True,
        log_level="info",
    )


