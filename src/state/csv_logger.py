"""
csv_logger.py — Thread-safe CSV audit logger.
Logs all messages (customer, ai, agent) to chat_data.csv at project root.
"""

import csv
import os
import threading
from datetime import datetime, timezone
import asyncio

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CSV_PATH = os.path.join(_PROJECT_ROOT, "chat_data.csv")
FIELDNAMES = ["timestamp", "phone", "role", "message", "qdrant_sources", "retrieval_query"]

_csv_lock = threading.Lock()
_schema_ready = False


def _ensure_csv_exists():
    global _schema_ready
    if _schema_ready:
        return

    with _csv_lock:
        if _schema_ready:
            return

        if not os.path.exists(CSV_PATH):
            with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
                writer.writeheader()
            print(f"[csv_logger] Created new CSV: {CSV_PATH}")
        else:
            try:
                with open(CSV_PATH, "r", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    existing_fields = list(reader.fieldnames or [])

                if existing_fields != FIELDNAMES:
                    with open(CSV_PATH, "r", encoding="utf-8") as f:
                        reader = csv.DictReader(f)
                        rows = list(reader)

                    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
                        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
                        writer.writeheader()
                        for row in rows:
                            writer.writerow({k: row.get(k, "") for k in FIELDNAMES})

                    print(f"[csv_logger] Migrated CSV: {existing_fields} → {FIELDNAMES} ({len(rows)} rows preserved)")
            except Exception as e:
                print(f"[csv_logger] Schema migration failed (non-fatal): {e}")

        _schema_ready = True


def log_message(phone: str, role: str, message: str, sources: str = "", retrieval_query: str = ""):
    _ensure_csv_exists()

    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "phone": phone,
        "role": role,
        "message": message,
        "qdrant_sources": sources,
        "retrieval_query": retrieval_query,
    }

    with _csv_lock:
        with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writerow(row)


async def log_message_async(phone: str, role: str, message: str, sources: str = "", retrieval_query: str = ""):
    return await asyncio.to_thread(log_message, phone, role, message, sources, retrieval_query)
