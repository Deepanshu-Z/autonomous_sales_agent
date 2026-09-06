"""
supabase_client.py — Supabase connection and cache management service.
"""

import os
import re
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase: Client = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        print("[supabase_client] Connected to Supabase successfully")
    except Exception as e:
        print(f"[supabase_client] Supabase connection error: {e}")
else:
    print("[supabase_client] SUPABASE_URL/SUPABASE_KEY not set — caching disabled.")


def normalize(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text


def check_cache(question: str):
    """
    Returns a cached answer string if this (normalized) question has
    been asked and answered before, otherwise None.
    """
    if supabase is None:
        return None

    try:
        key = normalize(question)
        result = (
            supabase.table("store_history")
            .select("answer")
            .eq("normalized_question", key)
            .limit(1)
            .execute()
        )
        if result.data:
            return result.data[0]["answer"]
        return None

    except Exception as e:
        print(f"[supabase_client] check_cache error: {e}")
        return None


def save_to_cache(question: str, answer: str):
    if supabase is None:
        return

    try:
        key = normalize(question)
        supabase.table("store_history").upsert(
            {
                "normalized_question": key,
                "question": question,
                "answer": answer,
            },
            on_conflict="normalized_question",
        ).execute()
    except Exception as e:
        print(f"[supabase_client] save_to_cache error: {e}")
