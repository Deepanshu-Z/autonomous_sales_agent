"""Self-check: everyday short words never lock a customer into another language (real case: "Ab" -> Punjabi).
Run: python scripts/check_language_match.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.core.intent_classifier import apply_extraction_to_state
from src.core.language_loader import match_language

for word in ("Ab", "Ka", "Na", "Ja", "Ma", "Ta", "In", "Am", "1", "2", "3", "english classes hai kya", "hindi nahi aati"):
    assert match_language(word) is None, (word, match_language(word))
for tap, lang in (("Hindi", "Hindi"), ("हिन्दी (Hindi)", "Hindi"), ("ਪੰਜਾਬੀ (Punjabi)", "Punjabi"), ("english", "English")):
    assert match_language(tap) == lang, (tap, match_language(tap))

# The exact turn from 919915220028: "Ab" after "App download ho gaya" keeps the customer in Hinglish.
s = apply_extraction_to_state({"course": "ladies_dance", "language": "Hinglish (Latin script)"},
                              {"customer_intent": "OTHER", "detected_language": "Hinglish (Latin script)", "_raw_text": "Ab"})
assert s["language"] == "Hinglish (Latin script)" and not s.get("language_selected"), s.get("language")
print("OK: short everyday words never switch the language; button taps and language names still do")
