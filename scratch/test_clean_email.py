import re
from typing import Optional

def clean_email(email_str: str, is_awaiting_email: bool = False) -> Optional[str]:
    """Extracts and normalizes email string (lowercased, stripped of surrounding punctuation)."""
    if not email_str:
        return None
    s = str(email_str).strip()
    
    # 1. Standard email pattern
    m = re.search(r"([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)", s)
    if m:
        extracted = m.group(1).lower().strip().rstrip(".")
        if extracted.endswith("@razorpay.com") or "void@" in extracted or "example.com" in extracted:
            return None
        return extracted
    
    # 2. Conversational handle with 'gmail': e.g. "this is my gmail: deepanshupokhriyal07", "gmail id: test1234"
    m_handle = re.search(r"(?:gmail(?:\s*id|\s*address)?\s*[:=]?\s*)([a-zA-Z0-9_.+-]{3,35})\b", s, re.IGNORECASE)
    if m_handle:
        handle = m_handle.group(1).lower().strip().rstrip(".")
        if not handle.endswith("@gmail.com"):
            return f"{handle}@gmail.com"
        return handle
        
    # 3. Bare alphanumeric username when awaiting email: e.g. "deepanshupokhriyal07"
    if is_awaiting_email:
        clean_handle = re.sub(r"[^a-zA-Z0-9_.+-]", "", s)
        if 4 <= len(clean_handle) <= 35 and any(c.isalpha() for c in clean_handle):
            # Guard against common conversational words like "okay", "thanks", "hello", "done"
            non_emails = {"okay", "ok", "theek", "accha", "sure", "thanks", "done", "yes", "ha", "nahi", "no", "hello", "hi", "yhi", "mra", "mera"}
            if clean_handle.lower() not in non_emails and not any(w in s.lower() for w in ["yhi h", "mera complete", "complete gmail"]):
                return f"{clean_handle.lower()}@gmail.com"
                
    return None

tests = [
    ("deepanshupokhriyal07@gmail.com", False, "deepanshupokhriyal07@gmail.com"),
    ("this is my gmail: deepanshupokhriyal07", False, "deepanshupokhriyal07@gmail.com"),
    ("gmail: deepanshupokhriyal07", False, "deepanshupokhriyal07@gmail.com"),
    ("deepanshupokhriyal07", True, "deepanshupokhriyal07@gmail.com"),
    ("yhi h mra complete gmail", True, None),
    ("okay", True, None),
    ("void@razorpay.com", False, None),
]

for t, awaiting, expected in tests:
    res = clean_email(t, is_awaiting_email=awaiting)
    print(f"'{t}' (awaiting={awaiting}) -> {res} (Expected: {expected})")
    assert res == expected, f"Failed for {t}: got {res}, expected {expected}"

print("ALL CLEAN_EMAIL TESTS PASSED!")

