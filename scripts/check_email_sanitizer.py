"""Self-check for email extraction, built from REAL paid-client messages (Nov 2026 audit).
Run: venv\\Scripts\\python.exe scripts\\check_email_sanitizer.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.core.helpers.email_sanitizer import sanitize_email

CASES = [
    # --- bugs seen in production ---
    ("None", None),                                                   # screenshot -> none@gmail.com
    ("Gmail ID-dashsatabdi2@gmail.com", "dashsatabdi2@gmail.com"),    # was gmailid-dashsatabdi2@
    ("Gmail ID\nsiana4jesus@gmail.com", "siana4jesus@gmail.com"),     # was gmailidsiana4jesus@
    ("My emai I'd....\nbharati.anu09@gmail.com", "bharati.anu09@gmail.com"),
    ("My emai I'd....bharati.anu09@gmail.com", "bharati.anu09@gmail.com"),
    ("I have log in with bharati.anu09@gmail.com", "bharati.anu09@gmail.com"),
    ("With correct gmail account", None),                             # was account@gmail.com
    ("ma'am gmail id koi nhi mangta", None),                          # was koi@gmail.com
    ("I paid via gmail account yesterday", None),
    ("my gmail is priya.k12", None),                                  # bare handle: ask again, don't guess
    ("Lakshanya rajendra @gmail. Com", None),                         # ambiguous: ask to retype
    # --- must keep working ---
    ("Sunthakumar998@ gmailcom", "sunthakumar998@gmail.com"),
    ("Renukak841@gmail.com", "renukak841@gmail.com"),
    ("dr.sugamtiwarisingh@gmail.com", "dr.sugamtiwarisingh@gmail.com"),
    ("Ji ye hai meri id: abc.def12@gmail.com please link", "abc.def12@gmail.com"),
    ("rahul123 @gmail.com", "rahul123@gmail.com"),
    ("rahul123@gmail", "rahul123@gmail.com"),
    ("rahul123@gamil.con", "rahul123@gmail.com"),
    ("priya@yahoo,com", "priya@yahoo.com"),
    ("x@yahoo.con", "x@yahoo.com"),                                   # was x@yahoo.co.in
    ("first.last-x@tcs.com", "first.last-x@tcs.com"),
    ("rahul123 gmail.com", "rahul123@gmail.com"),
    ("I paid with gmail.com", None),
    ("a@b.com c@d.com", None),
    ("[IMAGE]", None),
]

bad = [(t, exp, sanitize_email(t)) for t, exp in CASES if sanitize_email(t) != exp]
for t, exp, got in bad:
    print(f"FAIL {t!r}: expected {exp!r}, got {got!r}")
assert not bad, f"{len(bad)} case(s) failed"
print(f"OK: {len(CASES)} email sanitizer cases passed")

# Inbound media (screenshots) must be tagged, never blank / "None", and caption text kept.
from src.api.routes import _extract_message_text as extract
assert extract({"message_content_type": "Image", "message": None, "media_url": "u"}) == "[MEDIA:image]"
assert extract({"media_type": ["image"], "message": None}) == "[MEDIA:image]"
assert extract({"message_content_type": "Image", "message": "a1b2c3d@gmail.com"}) == "[MEDIA:image]\na1b2c3d@gmail.com"
assert extract({"message_content_type": "Text", "message": "None"}) == ""
assert extract({"message_content_type": "Text", "message": "hello"}) == "hello"
print("OK: media tagging checks passed")
