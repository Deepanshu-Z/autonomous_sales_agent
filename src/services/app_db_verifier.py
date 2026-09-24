"""
app_db_verifier.py — Real-time Production App Database Verification Service.

Queries the Sensationz production PostgreSQL RDS database (Drizzle ORM schema)
to verify whether a WhatsApp lead has actually downloaded the app,
created their profile, booked a trial, or enrolled in a course.

Zero hardcoding: Reads PROD_APP_DB_URL, PROD_APP_USERS_TABLE, PROD_APP_PHONE_COLUMN from environment.
Production-first: Uses asyncpg, connection pooling, 5s timeout, and graceful non-blocking fallback.
"""

import os
import re
import asyncio
from typing import Dict, Any, Optional
from dotenv import load_dotenv

load_dotenv()

PROD_APP_DB_URL = os.getenv("PROD_APP_DB_URL")
PROD_APP_USERS_TABLE = os.getenv("PROD_APP_USERS_TABLE", "profile")
PROD_APP_PHONE_COLUMN = os.getenv("PROD_APP_PHONE_COLUMN", "contact")

_pool = None
_pool_lock = asyncio.Lock()


async def _get_pool():
    global _pool
    if _pool is not None:
        return _pool

    if not PROD_APP_DB_URL:
        return None

    async with _pool_lock:
        if _pool is not None:
            return _pool
        try:
            import asyncpg
            _pool = await asyncpg.create_pool(
                PROD_APP_DB_URL,
                ssl="require",
                min_size=1,
                max_size=5,
                timeout=10,
                command_timeout=5,
            )
            print("[app_db_verifier] Production App DB connection pool established")
        except Exception as e:
            print(f"[app_db_verifier] Failed to connect to Production App DB: {e}")
            _pool = None
    return _pool


def normalize_phone_last10(phone: str) -> str:
    """Extracts only digits and returns the last 10 digits for robust matching."""
    if not phone:
        return ""
    digits = re.sub(r"\D", "", str(phone))
    last10 = digits[-10:] if len(digits) >= 10 else digits
    # Guard against invalid length or dummy test numbers like 0000000000 / 1111111111
    if len(last10) != 10 or len(set(last10)) <= 1:
        return ""
    return last10


async def verify_user_in_production_db_async(phone: str) -> Dict[str, Any]:
    """
    Asynchronously queries production PostgreSQL database for this user's profile.
    
    Returns structured verification result:
    {
        "verified": bool,           # True if profile exists in app
        "profile_id": str | None,
        "full_name": str | None,
        "email": str | None,
        "has_trial": bool,          # True if a trial class is booked
        "has_enrollment": bool,     # True if enrolled in a paid package
        "error": str | None
    }
    """
    last10 = normalize_phone_last10(phone)
    default_res = {
        "verified": False,
        "profile_id": None,
        "full_name": None,
        "email": None,
        "has_trial": False,
        "has_enrollment": False,
        "error": None,
    }

    if not last10:
        default_res["error"] = "Invalid phone number"
        return default_res

    pool = await _get_pool()
    if pool is None:
        default_res["error"] = "PROD_APP_DB_URL not configured or unreachable"
        return default_res

    try:
        async with pool.acquire() as conn:
            # 1. Query profile table by sanitized last 10 digits of contact, excluding deleted/placeholder accounts
            query_profile = f"""
                SELECT id, full_name, email, {PROD_APP_PHONE_COLUMN}
                FROM "{PROD_APP_USERS_TABLE}"
                WHERE RIGHT(regexp_replace({PROD_APP_PHONE_COLUMN}, '\\D', '', 'g'), 10) = $1
                  AND (full_name IS NULL OR (full_name NOT ILIKE 'deleted@%' AND full_name NOT ILIKE 'deleted_%'))
                ORDER BY created_at DESC NULLS LAST
                LIMIT 1;
            """
            profile_row = await conn.fetchrow(query_profile, last10)

            if not profile_row:
                return default_res  # User has not created a profile yet

            profile_id = profile_row["id"]
            default_res["verified"] = True
            default_res["profile_id"] = str(profile_id)
            default_res["full_name"] = profile_row["full_name"]
            default_res["email"] = profile_row["email"]

            # 2. Check if user has booked a trial
            try:
                trial_row = await conn.fetchrow(
                    'SELECT id FROM "trial" WHERE "profileId" = $1 LIMIT 1;',
                    profile_id
                )
                default_res["has_trial"] = bool(trial_row)
            except Exception:
                pass

            # 3. Check if user has an active course enrollment
            try:
                enroll_row = await conn.fetchrow(
                    'SELECT id FROM "enroll_course" WHERE "profile_id" = $1 LIMIT 1;',
                    profile_id
                )
                default_res["has_enrollment"] = bool(enroll_row)
            except Exception:
                pass

            return default_res

    except Exception as e:
        print(f"[app_db_verifier] Query error for {phone}: {e}")
        default_res["error"] = str(e)
        return default_res
