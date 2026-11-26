"""
trust_proof.py — the "Verify Sensationz yourself" card.

Sent when a customer doubts we are genuine (classifier flag `wants_trust_proof`). Everything in it comes
from data/global_rules.json -> organization (legal name, GSTIN, social links, trust_proof stats), so the
GSTIN and links are always exact — the AI never types them. A stat that is null/missing is simply left out;
nothing is ever estimated or invented.
"""

from typing import Any, Dict, Optional

from src.core import kb_loader

_LABELS: Dict[str, Dict[str, str]] = {
    "en": {
        "title": "✅ *Verify Sensationz yourself*",
        "gstin": "🧾 GSTIN",
        "verify": "🔎 Check it directly on the official GST portal (Govt. of India): open the link, tap *Search Taxpayer*, enter the GSTIN above:",
        "rating": "⭐ Google rating: *{rating}/5* from {count} reviews",
        "reviews": "📝 Read our Google reviews:",
        "follow": "📣 Follow us:",
        "followers": "followers",
        "website": "🌐 Website:",
        "about": "✅ *About Sensationz*",
        "since": "Since {year}",
        "students": "{n} students",
        "countries": "{n} countries",
        "gst_short": "(verify on the Govt. GST portal)",
        "office": "📍 Office:",
        "secure": "🔒 Payments are 100% secured by *Razorpay*",
        "instagram": "📸 Instagram:",
    },
    "hinglish": {
        "title": "✅ *Khud verify kijiye — Sensationz*",
        "gstin": "🧾 GSTIN",
        "verify": "🔎 Aap ise seedha official GST portal (Govt. of India) par check kar sakte hain: link kholiye, *Search Taxpayer* par tap kijiye aur upar wala GSTIN daaliye:",
        "rating": "⭐ Google rating: *{rating}/5* ({count} reviews)",
        "reviews": "📝 Hamare Google reviews padhiye:",
        "follow": "📣 Humse judiye:",
        "followers": "followers",
        "website": "🌐 Website:",
        "about": "✅ *Sensationz ke baare mein*",
        "since": "{year} se",
        "students": "{n} students",
        "countries": "{n} countries",
        "gst_short": "(Govt. GST portal par verify karein)",
        "office": "📍 Office:",
        "secure": "🔒 Payment *Razorpay* se 100% secure",
        "instagram": "📸 Instagram:",
    },
    "hindi": {
        "title": "✅ *खुद वेरिफ़ाई कीजिए — Sensationz*",
        "gstin": "🧾 GSTIN",
        "verify": "🔎 आप इसे सीधे आधिकारिक GST पोर्टल (भारत सरकार) पर जाँच सकते हैं: लिंक खोलिए, *Search Taxpayer* पर टैप कीजिए और ऊपर दिया GSTIN डालिए:",
        "rating": "⭐ Google रेटिंग: *{rating}/5* ({count} रिव्यू)",
        "reviews": "📝 हमारे Google रिव्यू पढ़िए:",
        "follow": "📣 हमसे जुड़िए:",
        "followers": "फ़ॉलोअर्स",
        "website": "🌐 वेबसाइट:",
        "about": "✅ *Sensationz के बारे में*",
        "since": "{year} से",
        "students": "{n} छात्र",
        "countries": "{n} देश",
        "gst_short": "(सरकारी GST पोर्टल पर जाँचें)",
        "office": "📍 ऑफ़िस:",
        "secure": "🔒 पेमेंट *Razorpay* से 100% सुरक्षित",
        "instagram": "📸 Instagram:",
    },
}

_SOCIALS = (("instagram", "Instagram"), ("facebook", "Facebook"), ("youtube", "YouTube"))


def _pick_labels(language: Optional[str]) -> Dict[str, str]:
    lang = (language or "").lower()
    if "devanagari" in lang or lang.strip() == "hindi":
        return _LABELS["hindi"]
    if "hinglish" in lang:
        return _LABELS["hinglish"]
    return _LABELS["en"]  # English and every other language: the card is mostly names and links


def _fmt_count(n: Any) -> Optional[str]:
    """Round DOWN so we never overstate: 125400 -> '125K+', 1_500_000 -> '1.5M+', 980 -> '980+'."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    if n >= 1_000_000:
        v = n // 100_000 / 10
        return f"{v:g}M+"
    if n >= 1_000:
        return f"{n // 1_000}K+"
    return f"{n}+"


def build_trust_card(language: Optional[str] = None, short: bool = False) -> str:
    """The card text, or "" when we have nothing verifiable to show (no legal name / GSTIN in the data).
    short=True: the compact "About Sensationz" card sent once with the first QR (one link, so the QR stays the focus).
    Default: the full card for a customer who doubts us (every way to verify)."""
    org: Dict[str, Any] = (kb_loader.get_global_rules() or {}).get("organization") or {}
    legal = org.get("legal_name") or org.get("name")
    gstin = org.get("registration_number_gstin")
    if not (legal and gstin):
        return ""
    proof: Dict[str, Any] = org.get("trust_proof") or {}
    social: Dict[str, Any] = org.get("social_media") or {}
    lb = _pick_labels(language)

    # Facts straight from the data; a missing one is left out, never estimated.
    facts = [lb["since"].format(year=org["established"])] if org.get("established") else []
    if org.get("students_trained"):
        facts.append(lb["students"].format(n=org["students_trained"]))
    if org.get("countries_served"):
        facts.append(lb["countries"].format(n=org["countries_served"]))
    since_line = ("🏆 " + " · ".join(facts)) if facts else ""
    branches = org.get("physical_branches") or []
    office = branches[0].get("address") if branches and isinstance(branches[0], dict) else None
    rating, review_count = proof.get("google_rating"), proof.get("google_review_count")
    try:
        reviews = f"{int(review_count):,}" if review_count else None  # exact (1,366), never rounded up
    except (TypeError, ValueError):
        reviews = None
    rating_line = lb["rating"].format(rating=rating, count=reviews) if rating and reviews else None
    if rating_line and proof.get("stats_as_of"):
        rating_line += f" ({proof['stats_as_of']})"

    if short:
        lines = [lb["about"], ""]
        if since_line:
            lines.append(since_line)
        lines += [f"🏢 {legal}", f"{lb['gstin']}: *{gstin}* {lb['gst_short']}"]
        if office:
            lines.append(f"{lb['office']} {office}")
        if rating_line:
            lines.append(rating_line)
        lines.append(lb["secure"])
        if social.get("instagram"):
            lines += ["", f"{lb['instagram']} {social['instagram']}"]
        return "\n".join(lines)

    lines = [lb["title"], ""]
    if since_line:
        lines.append(since_line)
    lines += [f"🏢 *{legal}*", f"{lb['gstin']}: *{gstin}*"]
    if proof.get("gst_verify_url"):
        lines += ["", f"{lb['verify']}\n{proof['gst_verify_url']}"]
    if office:
        lines += ["", f"{lb['office']} {office}"]

    if rating_line:
        lines += ["", rating_line]
    if social.get("google_reviews"):
        lines += [f"{lb['reviews']} {social['google_reviews']}"]
    lines += ["", lb["secure"]]

    followers = proof.get("followers") or {}
    social_lines = []
    for key, name in _SOCIALS:
        url = social.get(key)
        if not url:
            continue
        cnt = _fmt_count(followers.get(key))
        social_lines.append(f"• {name}: {url}" + (f" ({cnt} {lb['followers']})" if cnt else ""))
    if social_lines:
        lines += ["", lb["follow"], *social_lines]
    if social.get("website"):
        lines += ["", f"{lb['website']} {social['website']}"]
    return "\n".join(lines)
