"""
chat_history.py — Message history persistence and caching (Supabase + Redis).
"""

import os
import json
import asyncio
from dotenv import load_dotenv

from src.services.supabase_client import supabase
from src.services.redis_client import get_redis_connection

load_dotenv()

redis_conn = get_redis_connection()

HISTORY_CACHE_TTL = 60 * 30    # 30 minutes
HISTORY_CACHE_LIMIT = 24       # retain last ~8-10 conversational turns (1 user + 2 assistant messages per turn)


def _cache_key(phone: str) -> str:
    return f"history:{phone}"


def save_message(phone: str, role: str, text: str, response_time_sec: float = None):
    text_clean = (text or "").strip()
    if not text_clean:
        return

    payload = {"phone": phone, "role": role, "message": text_clean}
    if response_time_sec is not None:
        payload["response_time_sec"] = response_time_sec
        print(f"[chat_history] {phone} ({role}) - Response Time: {response_time_sec}s")

    if supabase is not None:
        try:
            supabase.table("chat_history").insert(payload).execute()
        except Exception as e:
            if response_time_sec is not None:
                try:
                    supabase.table("chat_history").insert({"phone": phone, "role": role, "message": text_clean}).execute()
                except Exception as ex:
                    print(f"[chat_history] Supabase insert failed: {ex}")
            else:
                print(f"[chat_history] Supabase insert failed (non-fatal): {e}")

    try:
        key = _cache_key(phone)
        cache_item = {"role": role, "content": text_clean}
        if response_time_sec is not None:
            cache_item["response_time_sec"] = response_time_sec
        redis_conn.rpush(key, json.dumps(cache_item))
        redis_conn.ltrim(key, -HISTORY_CACHE_LIMIT, -1)
        redis_conn.expire(key, HISTORY_CACHE_TTL)
    except Exception as e:
        print(f"[chat_history] Redis cache write failed (non-fatal): {e}")


def get_recent_history(phone: str, limit: int = HISTORY_CACHE_LIMIT):
    key = _cache_key(phone)

    try:
        cached = redis_conn.lrange(key, -limit, -1)
        if cached:
            parsed = [json.loads(item) for item in cached]
            return [turn for turn in parsed if (turn.get("content") or "").strip()]
    except Exception as e:
        print(f"[chat_history] Redis cache read failed, falling back to Supabase: {e}")

    if supabase is None:
        return []

    try:
        result = (
            supabase.table("chat_history")
            .select("role, message")
            .eq("phone", phone)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        history = [
            {"role": row["role"], "content": row["message"]}
            for row in reversed(result.data)
            if (row.get("message") or "").strip()
        ]
    except Exception as e:
        print(f"[chat_history] Supabase fetch history failed: {e}")
        return []

    try:
        pipe = redis_conn.pipeline()
        pipe.delete(key)
        for turn in history:
            pipe.rpush(key, json.dumps(turn))
        pipe.expire(key, HISTORY_CACHE_TTL)
        pipe.execute()
    except Exception as e:
        print(f"[chat_history] Redis cache warm-up failed (non-fatal): {e}")

    return history


def get_full_history_for_agent(phone: str):
    if supabase is None:
        return []
    try:
        result = (
            supabase.table("chat_history")
            .select("*")
            .eq("phone", phone)
            .order("created_at", desc=False)
            .execute()
        )
        return result.data
    except Exception as e:
        print(f"[chat_history] get_full_history_for_agent error: {e}")
        return []


async def save_message_async(phone: str, role: str, text: str, response_time_sec: float = None):
    return await asyncio.to_thread(save_message, phone, role, text, response_time_sec)


async def get_recent_history_async(phone: str, limit: int = HISTORY_CACHE_LIMIT):
    return await asyncio.to_thread(get_recent_history, phone, limit)


async def get_full_history_for_agent_async(phone: str):
    return await asyncio.to_thread(get_full_history_for_agent, phone)
