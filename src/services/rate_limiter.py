"""
rate_limiter.py — Independent Redis-based Rate Limiter Service.

Tracks message frequency per phone number to protect backend services (OpenAI, Interakt, AWS)
from spam, rapid loops, or automated attacks.

Design:
- Completely decoupled: No dependency on business logic, LLM, or conversation flow.
- Fail-open: If Redis is unavailable or errors, requests are allowed through so legitimate leads are never lost.
- Non-blocking: Uses async Redis (redis.asyncio) for sub-millisecond execution.
- Configurable: Parameters are loaded from environment variables.
"""

import os
import logging
from typing import Tuple, Dict, Any
from dotenv import load_dotenv
from src.services.redis_client import get_async_redis_connection

load_dotenv()

logger = logging.getLogger("rate_limiter")

# Default: 15 messages per 60 seconds per phone number
DEFAULT_MAX_MESSAGES = 15
DEFAULT_WINDOW_SECONDS = 60


def _get_config() -> Tuple[bool, int, int]:
    enabled = os.getenv("RATE_LIMIT_ENABLED", "true").strip().lower() in ("true", "1", "yes")
    try:
        max_msgs = int(os.getenv("RATE_LIMIT_MAX_MESSAGES", str(DEFAULT_MAX_MESSAGES)))
    except ValueError:
        max_msgs = DEFAULT_MAX_MESSAGES
    try:
        window_sec = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", str(DEFAULT_WINDOW_SECONDS)))
    except ValueError:
        window_sec = DEFAULT_WINDOW_SECONDS
    return enabled, max_msgs, window_sec


async def is_rate_limited(phone: str) -> Tuple[bool, Dict[str, Any]]:
    """
    Evaluates whether the given phone number has exceeded the message rate limit.

    Returns:
        (is_limited: bool, metadata: dict)
        metadata includes:
            - allowed: bool
            - count: current number of messages in the window
            - limit: maximum allowed messages
            - window: window size in seconds
            - retry_after: seconds remaining until window reset (if limited)
    """
    if not phone:
        return False, {"allowed": True, "count": 0, "limit": 0, "window": 0, "retry_after": 0}

    enabled, max_msgs, window_sec = _get_config()
    if not enabled:
        return False, {"allowed": True, "count": 0, "limit": max_msgs, "window": window_sec, "retry_after": 0}

    # Normalize phone key
    clean_phone = phone.strip().replace("+", "").replace(" ", "").replace("-", "")
    key = f"ratelimit:{clean_phone}"

    try:
        r = get_async_redis_connection()
        pipe = r.pipeline()
        pipe.incr(key)
        pipe.ttl(key)
        results = await pipe.execute()

        count = int(results[0])
        ttl = int(results[1])

        # If key is brand new or lacks expiration, set the TTL
        if count == 1 or ttl < 0:
            await r.expire(key, window_sec)
            ttl = window_sec

        if count > max_msgs:
            return True, {
                "allowed": False,
                "count": count,
                "limit": max_msgs,
                "window": window_sec,
                "retry_after": max(1, ttl),
            }

        return False, {
            "allowed": True,
            "count": count,
            "limit": max_msgs,
            "window": window_sec,
            "retry_after": 0,
        }

    except Exception as exc:
        # FAIL-OPEN: Log warning but never block user if Redis encounters an issue
        logger.warning(f"[rate_limiter] Failed to check rate limit for {phone} ({exc}). Permitting request (fail-open).")
        return False, {
            "allowed": True,
            "count": 0,
            "limit": max_msgs,
            "window": window_sec,
            "retry_after": 0,
            "error": str(exc),
        }


async def reset_rate_limit(phone: str) -> bool:
    """
    Resets the rate limit counter for a phone number (e.g. for testing or agent override).
    """
    if not phone:
        return False
    clean_phone = phone.strip().replace("+", "").replace(" ", "").replace("-", "")
    key = f"ratelimit:{clean_phone}"
    try:
        r = get_async_redis_connection()
        await r.delete(key)
        return True
    except Exception as exc:
        logger.warning(f"[rate_limiter] Failed to reset rate limit for {phone}: {exc}")
        return False
