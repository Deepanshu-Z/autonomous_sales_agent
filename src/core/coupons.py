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
    Dynamically identifies a course package duration from user text.
    Iterates over the active course's registry from JSON, matching against:
    1. Exact canonical name
    2. Explicit coupon code
    3. JSON-defined aliases
    4. Exact base or offer prices
    5. Dynamic numeric duration patterns derived from the canonical name.
    Guards against misinterpreting class timings (e.g. '6:00 AM', '1:00 PM').
    """
    if not text:
        return None
    t = re.sub(r"\s+", " ", text.strip().lower())

    registry = get_coupon_registry(course_id) or get_coupon_registry("yoga")
    if not registry:
        return None

    # 1. Exact canonical duration match
    for canonical_name in registry.keys():
        if t == canonical_name.lower():
            return canonical_name

    # 2. Check coupon codes from registry
    for canonical_name, data in registry.items():
        code = data.get("code", "").lower()
        if code and re.search(rf"\b{re.escape(code)}\b", t):
            return canonical_name

    # 3. Check JSON-defined aliases
    for canonical_name, data in registry.items():
        for alias in data.get("aliases", []):
            if re.search(rf"\b{re.escape(alias.lower())}\b", t):
                return canonical_name

    # 4. Check prices (base_price or offer_price in short text)
    if not re.search(r"\b(?:am|pm)\b", t):
        for canonical_name, data in registry.items():
            base = str(data.get("base_price", ""))
            offer = str(data.get("offer_price", ""))
            if (base and base in t) or (offer and offer in t):
                if len(t) <= 30 or re.search(rf"₹\s*(?:{base}|{offer})", t):
                    return canonical_name

    # 5. Dynamic numeric + duration unit match
    # Sort with longer durations first (e.g. Years / 12 Months before 1 Month) to avoid prefix collision
    def _duration_sort_key(item):
        name = item[0].lower()
        nums = re.findall(r"\d+", name)
        val = int(nums[0]) if nums else 1
        if "year" in name or "yr" in name:
            return val * 365
        elif "month" in name:
            return val * 30
        elif "week" in name:
            return val * 7
        return val

    sorted_packages = sorted(registry.items(), key=_duration_sort_key, reverse=True)

    for canonical_name, data in sorted_packages:
        name_lower = canonical_name.lower()
        nums = re.findall(r"\d+", canonical_name)
        num = nums[0] if nums else None

        if "year" in name_lower and num:
            # Matches "1 year", "1 yr", "1y", "1 saal", or "12 months", "12m"
            if re.search(rf"\b(?:{num}\s*(?:years?|year|yr|yrs|y|saal)|(?:12\s*(?:months?|month|m|mahine?)))\b", t):
                return canonical_name
            if re.match(rf"^(?:{num}y|{num}yr|{num}\s*year|12m|12)$", t):
                return canonical_name

        elif "month" in name_lower and num:
            # Guard against clock times for 1 Month (e.g. '1:00 pm', '1 pm', '10:00 am')
            if num == "1" and re.search(r"\b1(?::00)?\s*(?:pm|am)\b", t):
                continue
            if re.search(rf"\b{num}\s*(?:months?|month|m|mahina|mahine)\b", t):
                return canonical_name
            if re.match(rf"^(?:{num}|{num}m|{num}\s*months?)$", t):
                return canonical_name

        elif "week" in name_lower and num:
            if re.search(rf"\b{num}\s*(?:weeks?|week|w|hafta|hafte)\b", t):
                return canonical_name

        elif "day" in name_lower and num:
            if re.search(rf"\b{num}\s*(?:days?|day|d|din)\b", t):
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
    elif any(k in str(language).lower() for k in ["hindi", "hinglish"]):
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


def build_coupon_context(state: Optional[Dict[str, Any]] = None, course_id: str = "yoga") -> str:
    """
    Backend Secret Gating Architecture:
    - If profile is NOT created: Codes are strictly locked and hidden from LLM context.
      Shows offer prices only. Instructs LLM to guide to profile creation and emit
      the action tag [UNLOCK_COUPON] if customer indicates profile setup.
    - If profile IS created: Unlocked codes are injected into context for natural conversation.
    """
    s = state or {}
    profile_created = bool(
        s.get("profile_created")
        or s.get("coupon_sent")
        or s.get("stage") in ("PROFILE_COMPLETED", "COUPON_SENT")
    )

    course_kb = kb_loader.get_course_data(course_id)
    packages = course_kb.get("pricing", {}).get("packages", [])
    profile_video = course_kb.get("universal_enrollment_flow", {}).get("how_to_create_profile_video_url")

    if not profile_created:
        lines = [
            "• GANESH CHATURTHI OFFER — DISCOUNT POLICY (PROFILE REGISTRATION REQUIRED):",
            "  - Ganesh Chaturthi Offer coupon codes are LOCKED and hidden. Codes unlock ONLY AFTER profile registration.",
            "  - ZERO FABRICATION RULE: You do NOT have any coupon codes. NEVER invent, fabricate, guess, or output ANY coupon code string (e.g. NEVER make up codes like 'WELCOME100', 'DISCOUNT50', or ANY code string).",
            "  - If customer asks for a coupon or code for ANY package (e.g. '6 month ka coupon do', 'coupon code kya hai', 'give coupon'):",
            "    1. Warmly explain that Ganesh Chaturthi Offer coupon codes unlock only AFTER completing profile registration on the Sensationz App or website.",
            "    2. Quote the official offer price for their package (e.g. 6 Months offer price is \u20b91,000, 1 Month is \u20b9300).",
            "    3. Guide them to complete their profile on the Sensationz App or website using the official links:",
        ]
        if profile_video:
            lines.append(f"       \u2022 Tutorial Video: {profile_video}")
        lines.extend([
            "       \u2022 Android App: https://play.google.com/store/apps/details?id=com.sensationz.sensationz.dev",
            "       \u2022 iOS App: https://apps.apple.com/us/app/sensationz/id6761418351",
            "       \u2022 Website: https://shop.sensationzperformingarts.com/",
            "  - PROFILE COMPLETION TRIGGER (STRICT PAST TENSE AFFIRMATION ONLY):",
            "    \u2022 Emit [UNLOCK_COUPON] ONLY IF the customer states they ALREADY finished/created/completed their profile or registered.",
            "    \u2022 STRICT NEGATIVE RULE (NO [UNLOCK_COUPON] ON QUESTIONS/INQUIRIES): If the customer asks WHERE, HOW, or WHEN to create their profile (e.g. 'kha bnani h', 'kahan banegi', 'kaise banayein', 'where do I make it', 'how to create', 'send link'), they have NOT created it! NEVER output [UNLOCK_COUPON] on questions, inquiries, or instructions. Guide them to download the app or visit the website instead.",
            "    \u2022 CELEBRATE warmly and include [UNLOCK_COUPON] ONLY when they confirm completion (e.g. 'Congratulations! [UNLOCK_COUPON]').",
            "\u2022 OFFICIAL OFFER PRICES — GANESH CHATURTHI OFFER (Coupon codes are securely hidden until profile created):"
        ])
        for pkg in packages:
            lines.append(f"  \u2022 {pkg.get('duration')}: Offer Price \u20b9{pkg.get('offer_price')} (Regular: \u20b9{pkg.get('base_price')}) \u2014 [Code locked until profile created]")
        if not packages:
            lines.append("  (No course selected yet \u2014 once customer selects a course, offer prices and locked codes will be shown.)")
        return "\n".join(lines)

    elif profile_created and s.get("wants_trial") and not s.get("package") and not s.get("is_coupon_requested"):
        return (
            "• CUSTOMER STATUS — PROFILE CREATED FOR FREE TRIAL:\n"
            "  - The customer has completed their profile registration specifically to attend a FREE TRIAL class.\n"
            "  - DO NOT pitch paid packages or output discount coupon codes right now! The customer is here for their free trial class.\n"
            "  - Congratulate them warmly on creating their profile and instruct them to finalize their free trial in the Sensationz App: "
            "Profile -> Trial Links -> Select batch timing -> Tap Confirm Trial Enrollment.\n"
            "  - Ask if they are all set for their trial session or have any questions."
        )

    else:
        selected = s.get("package")
        if not packages:
            return (
                "• GANESH CHATURTHI OFFER — PROFILE CONFIRMED (UNLOCKED):\n"
                "  - Profile is confirmed but no course is selected yet. Coupon codes are course-specific.\n"
                "  - DO NOT output any coupon code. Once the customer selects their course and package, share the exact code from COURSE_DATA.\n"
                "  - ZERO FABRICATION RULE: NEVER invent, guess, or make up any coupon code string!"
            )
        lines = [
            "• GANESH CHATURTHI OFFER — PROFILE CONFIRMED (UNLOCKED):",
            "  - Customer's profile registration is CONFIRMED. Ganesh Chaturthi Offer coupon codes are UNLOCKED.",
            "  - Below are the exact unlocked coupon codes for each package duration:",
        ]
        for p in packages:
            lines.append(f"  • {p.get('duration')}: *{p.get('coupon_code')}* (Regular: ₹{p.get('base_price')}, Offer Price: ₹{p.get('offer_price')})")

        if selected:
            canonical_sel = normalize_package_name(selected, course_id=course_id) or selected.strip()
            pkg = next((p for p in packages if normalize_package_name(p.get("duration"), course_id=course_id) == canonical_sel or str(p.get("duration", "")).lower() == canonical_sel.lower()), None)
            if pkg:
                lines.append(f"\n  - Customer's Selected Package: {pkg.get('duration')}. Share their code *{pkg.get('coupon_code')}* and checkout instructions.")
        else:
            lines.append("\n  - Share the coupon code corresponding to whichever duration the customer chooses or asks about.")
        lines.append("  - NEVER invent or modify codes. Use ONLY the exact codes listed above.")
        return "\n".join(lines)


def format_coupon_unlock_delivery(
    package_name: Optional[str] = None,
    course_id: str = "yoga",
    language: str = "hinglish"
) -> str:
    """
    Returns the clean coupon code delivery block to replace [UNLOCK_COUPON]
    when intercepted by the backend.
    """
    details = get_coupon_details(package_name, course_id=course_id)
    canonical = normalize_package_name(package_name, course_id=course_id)
    registry = get_coupon_registry(course_id)

    if details:
        code = details["code"]
        offer = details["offer_price"]
        if any(k in str(language).lower() for k in ["hindi", "hinglish"]):
            return (
                f"\n\nYe raha aapka Ganesh Chaturthi Offer coupon code 🎁🪔\n"
                f"✨ Coupon Code: *{code}* ({canonical} package ke liye — Offer Price: ₹{offer})\n\n"
                "Isko Sensationz App ya website checkout par enter karke apply karein. Class mein milte hain! ✨"
            )
        else:
            return (
                f"\n\nHere is your Ganesh Chaturthi Offer coupon code 🎁🪔\n"
                f"✨ Coupon Code: *{code}* (for {canonical} package — Offer Price: ₹{offer})\n\n"
                "Please enter this code during checkout in the Sensationz App or website to activate your discount. See you in class! ✨"
            )
    else:
        lines = [f"• {pkg}: *{data['code']}* (Offer: ₹{data['offer_price']})" for pkg, data in registry.items()]
        codes_block = "\n".join(lines)
        if any(k in str(language).lower() for k in ["hindi", "hinglish"]):
            return (
                f"\n\nYe rahe aapke Ganesh Chaturthi Offer coupon codes 🎁🪔\n"
                f"{codes_block}\n\n"
                "Apne selected package ke liye coupon code Sensationz App ya website checkout par enter karke apply karein. Class mein milte hain! ✨"
            )
        else:
            return (
                f"\n\nHere are your Ganesh Chaturthi Offer coupon codes 🎁🪔\n"
                f"{codes_block}\n\n"
                "Please enter the coupon code for your selected package during checkout in the Sensationz App or website. See you in class! ✨"
            )
