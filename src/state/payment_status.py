"""
payment_status.py — SINGLE SOURCE OF TRUTH for "has this client paid?".

Kept dependency-free on purpose: prompt, slot_engine, intent_classifier, workers and the
Razorpay service all import it, and those sit in an import cycle with chat_state/kb_loader.
"""

from typing import Any, Optional


def is_payment_verified(state: Optional[dict[str, Any]]) -> bool:
    """
    True only when Razorpay confirmed a captured payment.

    `payment_status`, `stage == "ENROLLED"` and `is_enrolled` are written exclusively by
    `razorpay_service.fulfill_razorpay_payment_async` (webhook or QR lookup) — never by the
    LLM, intent extraction or a client's claim. Use this everywhere instead of re-implementing
    the check.
    """
    if not isinstance(state, dict):
        return False
    return (
        state.get("payment_status") == "PAID"
        or state.get("stage") == "ENROLLED"
        or state.get("is_enrolled") is True
    )
