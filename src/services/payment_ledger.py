"""
payment_ledger.py — permanent ledger of CAPTURED Razorpay payments (Supabase `payments` table).

Design
------
* Rows are built ONLY from Razorpay payment entities and validated here with the same rules as
  the table constraints (docs/sql/create_payments_table.sql), so no dummy / partial data is written.
* Writes never block the AI or webhook pipeline: callers use `schedule_*` (fire-and-forget tasks
  with timeout + retries). A write that still fails goes to a durable Redis retry queue.
* Fallbacks run in the background in the whatsapp-followup-worker process
  (`run_ledger_maintenance_forever`): drain the retry queue, and reconcile recent Razorpay QR
  payments that never reached the ledger (dropped webhook / crashed task).
* Client "PAID" status is NOT read from here — `payment_status.is_payment_verified(state)` stays
  the fast path. This table is the audit record: who paid, which payment, how much, when.
"""

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from src.core.helpers.email_sanitizer import sanitize_email

logger = logging.getLogger(__name__)

TABLE = "payments"
SOURCES = {"webhook", "claim_lookup", "reconciler", "backfill"}
WRITE_TIMEOUT_SEC = 5.0
WRITE_ATTEMPTS = 3                      # 1 try + 2 retries, inside the background task
RETRY_QUEUE_KEY = "payment_ledger:retry"
DEAD_LETTER_KEY = "payment_ledger:dead"
MAX_QUEUE_ATTEMPTS = 5                  # after this, the item moves to the dead-letter list
RETRY_DRAIN_INTERVAL_SEC = int(os.getenv("PAYMENT_LEDGER_RETRY_INTERVAL_SEC", "300"))
RECONCILE_INTERVAL_SEC = int(os.getenv("PAYMENT_LEDGER_RECONCILE_INTERVAL_SEC", "1800"))
RECONCILE_LOOKBACK_HOURS = int(os.getenv("PAYMENT_LEDGER_RECONCILE_HOURS", "48"))
SALES_QR_SOURCES = {"whatsapp_sales_agent", "sales_agent"}
# Team test QRs are ₹1 (29 of them in Oct-Nov 2026); the cheapest real package is ₹315.
MIN_REAL_PAYMENT_INR = float(os.getenv("PAYMENT_LEDGER_MIN_AMOUNT_INR", "100"))

_PAYMENT_ID = re.compile(r"^pay_[A-Za-z0-9]+$")
_QR_ID = re.compile(r"^qr_[A-Za-z0-9]+$")
_background_tasks: set = set()  # strong refs so fire-and-forget tasks aren't garbage-collected


class LedgerRowError(ValueError):
    """The data is not a real, complete captured payment — never written."""


# ── Pure helpers ──────────────────────────────────────────────────────────────

def is_ledger_enabled() -> bool:
    """Test-mode Razorpay keys produce fake payments: never record them."""
    return not os.getenv("RAZORPAY_KEY_ID", "").startswith("rzp_test_")


def normalize_phone(phone: Any) -> str:
    """Digits only, Indian 10-digit numbers prefixed with 91 (matches the table constraint)."""
    digits = re.sub(r"\D", "", str(phone or ""))
    return f"91{digits}" if len(digits) == 10 else digits


def build_payment_row(
    payment: Dict[str, Any],
    qr: Optional[Dict[str, Any]],
    phone: Any,
    source: str,
    course_id: Optional[str] = None,
    package: Optional[str] = None,
    timing: Optional[str] = None,
    teacher: Optional[str] = None,
    student_email: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a validated row from a Razorpay payment entity. Raises LedgerRowError otherwise."""
    qr = qr or {}
    notes = {**(qr.get("notes") or {}), **(payment.get("notes") or {})}
    payment_id = str(payment.get("id") or "")
    phone_digits = normalize_phone(phone)
    amount_paise = payment.get("amount") or 0
    course = (course_id or notes.get("course_id") or "").strip().lower()
    qr_id = qr.get("id") or notes.get("qr_id")

    if source not in SOURCES:
        raise LedgerRowError(f"unknown source {source!r}")
    if not _PAYMENT_ID.match(payment_id):
        raise LedgerRowError(f"not a Razorpay payment id: {payment_id!r}")
    if payment.get("status") != "captured":
        raise LedgerRowError(f"payment {payment_id} not captured (status={payment.get('status')!r})")
    if not isinstance(amount_paise, int) or amount_paise <= 0:
        raise LedgerRowError(f"payment {payment_id} has no amount")
    if amount_paise < MIN_REAL_PAYMENT_INR * 100:
        raise LedgerRowError(f"payment {payment_id} is a test amount (₹{amount_paise / 100:g} < ₹{MIN_REAL_PAYMENT_INR:g})")
    if not 10 <= len(phone_digits) <= 15:
        raise LedgerRowError(f"payment {payment_id} has no valid phone ({phone!r})")
    if not course:
        raise LedgerRowError(f"payment {payment_id} has no course")
    if not payment.get("created_at"):
        raise LedgerRowError(f"payment {payment_id} has no created_at")

    row = {
        "payment_id": payment_id,
        "phone": phone_digits,
        "qr_id": qr_id if qr_id and _QR_ID.match(str(qr_id)) else None,
        "amount_inr": round(amount_paise / 100.0, 2),
        "currency": payment.get("currency") or "INR",
        "status": "captured",
        "method": payment.get("method"),
        "course_id": course,
        "package": package or notes.get("duration"),
        "timing": timing or notes.get("timing"),
        "teacher": teacher or notes.get("teacher"),
        "student_email": sanitize_email(student_email) if student_email else None,
        "source": source,
        "captured_at": datetime.fromtimestamp(int(payment["created_at"]), tz=timezone.utc).isoformat(),
    }
    # Drop empty optional fields so an upsert never overwrites known data (e.g. a Gmail) with NULL.
    return {k: v for k, v in row.items() if v not in (None, "")}


# ── Supabase I/O (sync client → run in a thread) ─────────────────────────────

def _supabase():
    from src.services.supabase_client import supabase
    if supabase is None:
        raise RuntimeError("Supabase client not configured")
    return supabase


def _upsert_rows_sync(rows: List[Dict[str, Any]]) -> None:
    _supabase().table(TABLE).upsert(rows, on_conflict="payment_id").execute()


def _set_email_sync(phone_digits: str, email: Optional[str]) -> None:
    _supabase().table(TABLE).update({"student_email": email}).eq("phone", phone_digits).execute()


def _existing_payment_ids_sync(payment_ids: List[str]) -> set:
    if not payment_ids:
        return set()
    res = _supabase().table(TABLE).select("payment_id").in_("payment_id", payment_ids).execute()
    return {r["payment_id"] for r in (res.data or [])}


async def _run_with_retries(fn, *args) -> None:
    """Run a sync DB call in a thread with timeout and exponential backoff; raise the last error."""
    for attempt in range(1, WRITE_ATTEMPTS + 1):
        try:
            await asyncio.wait_for(asyncio.to_thread(fn, *args), timeout=WRITE_TIMEOUT_SEC)
            return
        except Exception as exc:
            if attempt == WRITE_ATTEMPTS:
                raise
            logger.warning(f"[payment_ledger] {fn.__name__} attempt {attempt} failed: {exc}")
            await asyncio.sleep(0.5 * 2 ** attempt)


# ── Durable retry queue (Redis) ───────────────────────────────────────────────

async def _enqueue_retry(op: str, payload: Dict[str, Any], attempts: int = 0) -> None:
    item = json.dumps({"op": op, "payload": payload, "attempts": attempts, "queued_at": int(time.time())})
    try:
        from src.services.redis_client import get_async_redis_connection
        await get_async_redis_connection().rpush(RETRY_QUEUE_KEY, item)
    except Exception as exc:  # last resort: the reconciler will still find the payment on Razorpay
        logger.error(f"[payment_ledger] Could not queue {op} for retry ({exc}). Item: {item}")


# ── Public write API ──────────────────────────────────────────────────────────

async def record_payment_async(row: Dict[str, Any]) -> bool:
    """Upsert one validated row. Never raises: on failure the row goes to the retry queue."""
    try:
        await _run_with_retries(_upsert_rows_sync, [row])
        logger.info(f"[payment_ledger] Recorded {row['payment_id']} ({row['source']}) for {row['phone']}")
        return True
    except Exception as exc:
        logger.error(f"[payment_ledger] Upsert failed for {row.get('payment_id')}: {exc}. Queued for retry.")
        await _enqueue_retry("upsert", row)
        return False


async def attach_student_email_async(phone: Any, email: Optional[str]) -> bool:
    """Set (or clear, with None) the Gmail on the client's payment rows. Never raises."""
    phone_digits = normalize_phone(phone)
    clean_email = sanitize_email(email) if email else None
    if email and not clean_email:
        logger.warning(f"[payment_ledger] Refusing invalid email for {phone_digits}: {email!r}")
        return False
    try:
        await _run_with_retries(_set_email_sync, phone_digits, clean_email)
        return True
    except Exception as exc:
        logger.error(f"[payment_ledger] Email update failed for {phone_digits}: {exc}. Queued for retry.")
        await _enqueue_retry("email", {"phone": phone_digits, "email": clean_email})
        return False


def _spawn(coro) -> None:
    task = asyncio.get_running_loop().create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def schedule_payment_record(payment: Dict[str, Any], qr: Optional[Dict[str, Any]], phone: Any,
                            source: str, **fields: Any) -> None:
    """Fire-and-forget: validate now (cheap), write in the background. Never raises to the caller."""
    if not is_ledger_enabled():
        return
    try:
        row = build_payment_row(payment, qr, phone, source, **fields)
    except LedgerRowError as exc:
        logger.warning(f"[payment_ledger] Not recorded: {exc}")
        return
    try:
        _spawn(record_payment_async(row))
    except RuntimeError:  # no running loop (sync caller) — never break the caller
        logger.error(f"[payment_ledger] No event loop; payment {row['payment_id']} left for the reconciler")


def schedule_email_attach(phone: Any, email: Optional[str]) -> None:
    """Fire-and-forget Gmail attach/clear on the client's payment rows."""
    if not is_ledger_enabled():
        return
    try:
        _spawn(attach_student_email_async(phone, email))
    except RuntimeError:
        logger.error(f"[payment_ledger] No event loop; email for {phone} not attached")


# ── Background fallbacks (run in whatsapp-followup-worker) ────────────────────

async def drain_retry_queue_async(max_items: int = 200) -> int:
    """Retry queued writes. Failures are re-queued; after MAX_QUEUE_ATTEMPTS → dead-letter list."""
    from src.services.redis_client import get_async_redis_connection
    redis = get_async_redis_connection()
    processed = 0
    for _ in range(max_items):
        raw = await redis.lpop(RETRY_QUEUE_KEY)
        if not raw:
            break
        item = json.loads(raw)
        processed += 1
        try:
            if item["op"] == "upsert":
                await _run_with_retries(_upsert_rows_sync, [item["payload"]])
            elif item["op"] == "email":
                await _run_with_retries(_set_email_sync, item["payload"]["phone"], item["payload"]["email"])
        except Exception as exc:
            item["attempts"] = item.get("attempts", 0) + 1
            target = DEAD_LETTER_KEY if item["attempts"] >= MAX_QUEUE_ATTEMPTS else RETRY_QUEUE_KEY
            await redis.rpush(target, json.dumps(item))
            logger.error(f"[payment_ledger] Retry {item['attempts']} failed for {item['op']}: {exc}"
                         + (" → DEAD-LETTER" if target == DEAD_LETTER_KEY else ""))
            if target == RETRY_QUEUE_KEY:
                break  # DB still down: stop this round instead of spinning
    return processed


def iter_sales_qr_codes(rzp_client, since_ts: int) -> Iterable[Dict[str, Any]]:
    """All QR codes this bot created since `since_ts` (paginated Razorpay listing)."""
    skip = 0
    while True:
        page = (rzp_client.qrcode.all({"from": since_ts, "count": 100, "skip": skip}) or {}).get("items", [])
        for qr in page:
            if (qr.get("notes") or {}).get("source") in SALES_QR_SOURCES:
                yield qr
        if len(page) < 100:
            return
        skip += 100


def captured_payments_for_qr(rzp_client, qr: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Captured payments on this QR for exactly its fixed amount."""
    if not qr.get("payments_amount_received") or not qr.get("payment_amount"):
        return []
    items = (rzp_client.qrcode.fetch_all_payments(qr["id"]) or {}).get("items", [])
    return [p for p in items if p.get("status") == "captured" and p.get("amount") == qr["payment_amount"]]


async def reconcile_recent_payments_async(lookback_hours: int = RECONCILE_LOOKBACK_HOURS,
                                          rzp_client=None) -> Dict[str, int]:
    """
    Safety net for dropped webhooks / crashed writes: every captured payment on our recent QRs
    that is missing from the ledger is fulfilled (if the client isn't PAID yet, which also sends
    their confirmation) or recorded directly (if they already are).
    """
    from src.services.razorpay_service import fulfill_razorpay_payment_async, get_razorpay_client
    from src.state.chat_state import get_user_state_async
    from src.state.payment_status import is_payment_verified

    stats = {"qr_checked": 0, "missing": 0, "fulfilled": 0, "recorded": 0}
    if not is_ledger_enabled():
        return stats
    client = rzp_client or get_razorpay_client()
    since = int(time.time()) - lookback_hours * 3600
    qrs = await asyncio.to_thread(lambda: list(iter_sales_qr_codes(client, since)))

    for qr in qrs:
        stats["qr_checked"] += 1
        payments = await asyncio.to_thread(captured_payments_for_qr, client, qr)
        existing = await asyncio.to_thread(_existing_payment_ids_sync, [p["id"] for p in payments])
        for payment in payments:
            if payment["id"] in existing:
                continue
            stats["missing"] += 1
            phone = (qr.get("notes") or {}).get("phone") or ""
            state = await get_user_state_async(phone)
            if not is_payment_verified(state):
                result = await fulfill_razorpay_payment_async(
                    {"event": "payment.captured",
                     "payload": {"payment": {"entity": payment}, "qr_code": {"entity": qr}}},
                    send_notification=True, ledger_source="reconciler",
                )
                stats["fulfilled"] += int(result.get("status") == "fulfilled")
                logger.warning(f"[payment_ledger] Reconciler fulfilled missed payment {payment['id']} for {phone}: {result.get('status')}")
            else:
                try:
                    row = build_payment_row(payment, qr, phone, "reconciler",
                                            student_email=state.get("student_email"))
                except LedgerRowError as exc:
                    logger.warning(f"[payment_ledger] Reconciler skipped {payment['id']}: {exc}")
                    continue
                stats["recorded"] += int(await record_payment_async(row))
    return stats


async def run_ledger_maintenance_forever() -> None:
    """Background loop for the worker process: retry queue every 5 min, reconcile every 30 min.
    Holds a Redis lock per run so two worker instances never reconcile at the same time."""
    from src.services.redis_client import get_async_redis_connection
    last_reconcile = 0.0
    while True:
        try:
            drained = await drain_retry_queue_async()
            if drained:
                logger.info(f"[payment_ledger] Retry queue: processed {drained} item(s)")
            if time.time() - last_reconcile >= RECONCILE_INTERVAL_SEC:
                last_reconcile = time.time()
                redis = get_async_redis_connection()
                if await redis.set("payment_ledger:reconcile_lock", "1", nx=True, ex=RECONCILE_INTERVAL_SEC - 60):
                    stats = await reconcile_recent_payments_async()
                    logger.info(f"[payment_ledger] Reconcile: {stats}")
        except Exception as exc:  # never let the maintenance loop die
            logger.error(f"[payment_ledger] Maintenance cycle failed: {exc}")
        await asyncio.sleep(RETRY_DRAIN_INTERVAL_SEC)
