"""
coupons.py — Centralized, Dynamic Coupon and Package Configuration Registry.
Derives packages, base fees, offer prices, coupon codes, and dynamic formatting
directly from structured course knowledge bases via kb_loader.
Zero hardcoded prices or codes in Python.
"""

from typing import Optional, Dict, Any, List
import re
from src.core import kb_loader


def get_coupon_registry(course_id: str = "yoga") -> Dict[str, Dict[str, Any]]:
    """Fetches coupons dynamically from structured course JSON via kb_loader."""
    return kb_loader.get_course_coupons(course_id)


# Module-level default exports for backward compatibility
COUPON_REGISTRY: Dict[str, Dict[str, Any]] = get_coupon_registry("yoga")
VALID_PACKAGES: Dict[str, str] = kb_loader.get_valid_packages("yoga")


def detect_package_from_text(text: Optional[str], course_id: str = "yoga") -> Optional[str]:
    """
    Robustly identifies a course package duration (1 Month, 3 Months, 6 Months, 1 Year)
    from user text, handling English, Hinglish, slang, coupon requests, and price mentions.
    Guards against misinterpreting class timings (e.g. '6:00 AM', '1:00 PM', '10:00 AM').
    Dynamically checks duration names, codes, aliases, and prices from the course JSON.
    """
    if not text:
        return None
    t = re.sub(r"\s+", " ", text.strip().lower())

    registry = get_coupon_registry(course_id) or get_coupon_registry("yoga")

    # 1. Exact canonical duration match
    for canonical_name in registry.keys():
        if t == canonical_name.lower():
            return canonical_name

    # 2. Check 1 Year / 12 Months first (to avoid '1' collision with 1 Month)
    for canonical_name, data in registry.items():
        if "year" in canonical_name.lower() or "12" in canonical_name:
            code = data.get("code", "").lower()
            base = str(data.get("base_price", ""))
            offer = str(data.get("offer_price", ""))
            if re.search(rf"\b(?:12\s*(?:months?|month|m|mahine?)|1\s*(?:years?|year|yr|yrs|y|saal)|one\s*year|ek\s*saal|yearly|annual|annually|{code}|₹\s*{base}|₹\s*{offer})\b", t):
                return canonical_name
            if (base in t or offer in t) and len(t) <= 25 and not re.search(r"\b(?:am|pm)\b", t):
                return canonical_name
            if re.match(r"^(?:12|12m|1y|1yr|1 year)$", t):
                return canonical_name

    # 3. Check 6 Months / Half-Yearly
    for canonical_name, data in registry.items():
        if "6" in canonical_name:
            code = data.get("code", "").lower()
            base = str(data.get("base_price", ""))
            offer = str(data.get("offer_price", ""))
            if re.search(rf"\b(?:6\s*(?:months?|month|m|mahine?)|six\s*months?|half\s*yearly|semi\s*annual|semi\s*annually|{code}|₹\s*{base}|₹\s*{offer})\b", t):
                return canonical_name
            if (base in t or offer in t) and len(t) <= 25 and not re.search(r"\b(?:am|pm)\b", t):
                return canonical_name
            if re.match(r"^(?:6|6m|6 month|6 months)$", t):
                return canonical_name

    # 4. Check 3 Months / Quarterly
    for canonical_name, data in registry.items():
        if "3" in canonical_name:
            code = data.get("code", "").lower()
            base = str(data.get("base_price", ""))
            offer = str(data.get("offer_price", ""))
            if re.search(rf"\b(?:3\s*(?:months?|month|m|mahine?)|three\s*months?|quarterly|{code}|₹\s*{base}|₹\s*{offer})\b", t):
                return canonical_name
            if (base in t or offer in t) and len(t) <= 25 and not re.search(r"\b(?:am|pm)\b", t):
                return canonical_name
            if re.match(r"^(?:3|3m|3 month|3 months)$", t):
                return canonical_name

    # 5. Check 1 Month (Guard against '1:00 pm', '1 pm', '10:00 am', '12:00 pm')
    if not re.search(r"\b1(?::00)?\s*(?:pm|am)\b", t):
        for canonical_name, data in registry.items():
            if "1 month" in canonical_name.lower():
                code = data.get("code", "").lower()
                base = str(data.get("base_price", ""))
                offer = str(data.get("offer_price", ""))
                if re.search(rf"\b(?:1\s*(?:months?|month|m|mahina|mahine)|one\s*month|monthly|{code}|₹\s*{base}|₹\s*{offer})\b", t):
                    return canonical_name
                if (base in t or offer in t) and len(t) <= 25:
                    return canonical_name
                if re.match(r"^(?:1|1m|1 month)$", t):
                    return canonical_name

    # 6. Check generic aliases from registry
    for canonical_name, data in registry.items():
        for alias in data.get("aliases", []):
            if re.search(rf"\b{re.escape(alias)}\b", t):
                return canonical_name

    return None


def normalize_package_name(text: Optional[str], course_id: str = "yoga") -> Optional[str]:
    """Finds the matching canonical package name from any string, alias, or sentence."""
    if not text:
        return None
    t = text.strip()
    registry = get_coupon_registry(course_id)
    for canonical_name in registry.keys():
        if t.lower() == canonical_name.lower():
            return canonical_name
    return detect_package_from_text(t, course_id=course_id)


def get_coupon_details(package_name: Optional[str], course_id: str = "yoga") -> Optional[Dict[str, Any]]:
    if not package_name:
        return None
    registry = get_coupon_registry(course_id)
    canonical = normalize_package_name(package_name, course_id=course_id)
    if canonical and canonical in registry:
        return registry[canonical]
    return None


def get_all_coupon_codes(course_id: str = "yoga") -> List[str]:
    registry = get_coupon_registry(course_id)
    return [data["code"] for data in registry.values() if data.get("code")]


def get_coupon_code_for_package(package_name: Optional[str], course_id: str = "yoga") -> Optional[str]:
    details = get_coupon_details(package_name, course_id=course_id)
    return details["code"] if details else None


def format_coupon_prompt_string(package_name: Optional[str], course_id: str = "yoga") -> str:
    registry = get_coupon_registry(course_id)
    all_codes_summary = ", ".join(
        f"{pkg}: *{data['code']}* (Offer: {data['currency']}{data['offer_price']:,})"
        for pkg, data in registry.items()
    )
    details = get_coupon_details(package_name, course_id=course_id)
    if details:
        canonical = normalize_package_name(package_name, course_id=course_id)
        return (
            f"Active Selected Package: {canonical} -> Code *{details['code']}* (Offer Price: {details['currency']}{details['offer_price']:,}).\n"
            f"   (ALL {course_id.title()} Package Coupon Codes: {all_codes_summary}.\n"
            f"   RULE: If customer asks about or chooses any different package duration, ALWAYS give the exact coupon code and offer price corresponding to that package!)"
        )
    return all_codes_summary


def format_coupon_banner(
    package_name: Optional[str],
    language: str = "hinglish",
    is_frustrated: bool = False,
    course_id: str = "yoga"
) -> str:
    details = get_coupon_details(package_name, course_id=course_id)
    canonical = normalize_package_name(package_name, course_id=course_id)
    registry = get_coupon_registry(course_id)

    if details:
        code = details["code"]
        code_details_hi = f"✨ Coupon Code: *{code}* ({canonical} package ke liye)"
        code_details_en = f"✨ Coupon Code: *{code}* (for {canonical} package)"
    else:
        lines = [f"• {pkg} duration: *{data['code']}*" for pkg, data in registry.items()]
        code_details_hi = "\n".join(lines)
        code_details_en = code_details_hi

    course_title = course_id.title()
    if is_frustrated:
        return (
            f"Bilkul denge! Hum daily live {course_title} classes conduct karte hain 😊\n\n"
            "Ye raha aapka special welcome discount coupon code 🎁\n\n"
            f"{code_details_hi}\n\n"
            "Isko Sensationz App ya website checkout par enter karke apply karein. Class mein milte hain! ✨"
        )
    elif language in ["hindi", "hinglish"]:
        return (
            "🎉 Sensationz family mein aapka swagat hai! 🌸\n"
            "Aapka welcome discount coupon code ye raha 🎁\n\n"
            f"{code_details_hi}\n\n"
            "Isko Sensationz App ya website checkout par enter karke apply karein aur offer price activate karein. Class mein milte hain! ✨"
        )
    else:
        return (
            "🎉 Welcome to the Sensationz family! 🌸\n"
            "Here is your welcome discount coupon code 🎁\n\n"
            f"{code_details_en}\n\n"
            "Please enter this code during checkout in the Sensationz App or website to activate your discount. See you in class! ✨"
        )
