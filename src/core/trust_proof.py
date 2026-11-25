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


def build_trust_card(language: Optional[str] = None) -> str:
    """The card text, or "" when we have nothing verifiable to show (no legal name / GSTIN in the data)."""
    org: Dict[str, Any] = (kb_loader.get_global_rules() or {}).get("organization") or {}
    legal = org.get("legal_name") or org.get("name")
    gstin = org.get("registration_number_gstin")
    if not (legal and gstin):
        return ""
    proof: Dict[str, Any] = org.get("trust_proof") or {}
    social: Dict[str, Any] = org.get("social_media") or {}
    lb = _pick_labels(language)

    lines = [lb["title"], "", f"🏢 *{legal}*", f"{lb['gstin']}: *{gstin}*"]
    if proof.get("gst_verify_url"):
        lines += ["", f"{lb['verify']}\n{proof['gst_verify_url']}"]

    rating, reviews = proof.get("google_rating"), _fmt_count(proof.get("google_review_count"))
    if rating and reviews:
        line = lb["rating"].format(rating=rating, count=reviews)
        if proof.get("stats_as_of"):
            line += f" ({proof['stats_as_of']})"
        lines += ["", line]
    if social.get("google_reviews"):
        lines += [f"{lb['reviews']} {social['google_reviews']}"]

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
