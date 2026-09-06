"""
kb_loader.py — Centralized Knowledge Base and Course Catalog Loader.

Provides reusable, cached access to:
1. Structured course knowledge bases (data/courses/{course_id}.json)
2. Global company and payment rules (data/global_rules.json)
3. Multi-course catalog registry (data/course_catalog.json)
4. Dynamic derivation of valid timings and packages (eliminating hardcoded lists)
"""

import os
import json
import re
from typing import Dict, Any, List, Optional

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DATA_DIR = os.path.join(_BASE_DIR, "data")
_COURSES_DIR = os.path.join(_DATA_DIR, "courses")

# In-memory caches to prevent redundant disk I/O
_COURSES_CACHE: Dict[str, Dict[str, Any]] = {}
_PROMPT_STR_CACHE: Dict[str, str] = {}
_GLOBALS_CACHE: Optional[Dict[str, Any]] = None
_CATALOG_CACHE: Optional[List[Dict[str, Any]]] = None


def get_course_data(course_id: str = "yoga") -> Dict[str, Any]:
    """
    Loads and caches structured course data from data/courses/{course_id}.json.
    Falls back to root {course_id}.json if data/courses/ does not exist.
    """
    cid = (course_id or "yoga").lower().strip()
    if cid in _COURSES_CACHE:
        return _COURSES_CACHE[cid]

    # Target path priority: data/courses/{cid}.json -> {cid}.json in workspace root
    primary_path = os.path.join(_COURSES_DIR, f"{cid}.json")
    fallback_path = os.path.join(_BASE_DIR, f"{cid}.json")

    filepath = primary_path if os.path.exists(primary_path) else fallback_path

    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
                _COURSES_CACHE[cid] = data
                return data
        except Exception as e:
            print(f"[kb_loader] Error reading {filepath}: {e}")

    # Check if the requested course is in the registered course catalog
    catalog = get_course_catalog()
    matching_course = next((c for c in catalog if c.get("id") == cid), None)
    if matching_course:
        course_name = matching_course.get("name", cid.title())
        catalog_entry = {
            "course": course_name,
            "status": "Available at Sensationz",
            "description": matching_course.get("description", ""),
            "has_full_schedule_in_chat": False,
            "how_to_enroll": "Available via Sensationz App & Website (https://shop.sensationzperformingarts.com/)",
            "ai_instruction": (
                f"We DO offer {course_name}! However, detailed batch schedules and fee structures for {course_name} "
                "are managed directly via the Sensationz App and website. NEVER state that this course is not offered! "
                "Guide the customer to the Sensationz App or website (https://shop.sensationzperformingarts.com/), "
                "or advise them to type *agent* so the department coordinator can provide the full schedule."
            )
        }
        _COURSES_CACHE[cid] = catalog_entry
        return catalog_entry

    # Fallback to yoga if completely unrecognized
    if cid != "yoga":
        return get_course_data("yoga")
    return {}


def get_course_prompt_str(course_id: str = "yoga", minified: bool = True) -> str:
    """
    Returns a cached JSON string of the course data optimized for prompt injection.
    Minified format saves ~1,200 tokens of whitespace indentation.
    """
    cid = (course_id or "yoga").lower().strip()
    cache_key = f"{cid}_{minified}"
    if cache_key in _PROMPT_STR_CACHE:
        return _PROMPT_STR_CACHE[cache_key]

    data = get_course_data(cid)
    if minified:
        prompt_str = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    else:
        prompt_str = json.dumps(data, indent=2, ensure_ascii=False)

    _PROMPT_STR_CACHE[cache_key] = prompt_str
    return prompt_str


def get_global_rules() -> Dict[str, Any]:
    """Loads shared studio addresses, apps, and universal policies."""
    global _GLOBALS_CACHE
    if _GLOBALS_CACHE is not None:
        return _GLOBALS_CACHE

    path = os.path.join(_DATA_DIR, "global_rules.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                _GLOBALS_CACHE = json.load(f)
                return _GLOBALS_CACHE
        except Exception as e:
            print(f"[kb_loader] Error loading global_rules.json: {e}")

    _GLOBALS_CACHE = {}
    return _GLOBALS_CACHE


def get_course_catalog() -> List[Dict[str, Any]]:
    """Loads registry of all available courses for multi-course routing."""
    global _CATALOG_CACHE
    if _CATALOG_CACHE is not None:
        return _CATALOG_CACHE

    path = os.path.join(_DATA_DIR, "course_catalog.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                _CATALOG_CACHE = json.load(f)
                return _CATALOG_CACHE
        except Exception as e:
            print(f"[kb_loader] Error loading course_catalog.json: {e}")

    _CATALOG_CACHE = []
    return _CATALOG_CACHE


def get_valid_timings(course_id: str = "yoga") -> List[str]:
    """
    Dynamically extracts the list of valid batch timings from the course JSON.
    Returns canonical format: ['5:00–6:00 AM', '6:00–7:00 AM', ...]
    """
    data = get_course_data(course_id)
    batches = data.get("batch_timings", {}).get("batches", [])
    valid_timings = []

    for b in batches:
        time_12h = b.get("time_12h", "")
        # Standardize '5:00 AM to 6:00 AM' -> '5:00–6:00 AM'
        m = re.match(r"(\d{1,2}:\d{2}\s*(?:AM|PM))\s*(?:to|-|–)\s*(\d{1,2}:\d{2}\s*(?:AM|PM))", time_12h, re.IGNORECASE)
        if m:
            start_str, end_str = m.group(1).strip(), m.group(2).strip()
            # If start and end have the same AM/PM, collapse canonical display e.g. 5:00–6:00 AM
            start_parts = start_str.split()
            end_parts = end_str.split()
            if len(start_parts) == 2 and len(end_parts) == 2 and start_parts[1].upper() == end_parts[1].upper():
                canonical = f"{start_parts[0]}–{end_parts[0]} {end_parts[1].upper()}"
            else:
                canonical = f"{start_str}–{end_str}"
            valid_timings.append(canonical)
        elif time_12h:
            valid_timings.append(time_12h)

    # Fallback to standard 10 yoga slots if JSON structure differed
    if not valid_timings and course_id == "yoga":
        return [
            "5:00–6:00 AM", "6:00–7:00 AM", "7:00–8:00 AM", "8:00–9:00 AM", "10:00–11:00 AM",
            "12:00–1:00 PM", "4:00–5:00 PM", "5:00–6:00 PM", "6:00–7:00 PM", "7:00–8:00 PM"
        ]
    return valid_timings


def get_valid_packages(course_id: str = "yoga") -> Dict[str, str]:
    """
    Dynamically extracts packages and formatted pricing from the course JSON.
    Returns e.g.:
    {
        '1 Month': '₹700 (Offer Price: ₹300)',
        '3 Months': '₹1,750 (Offer Price: ₹600)', ...
    }
    """
    data = get_course_data(course_id)
    packages = data.get("pricing", {}).get("packages", [])
    result = {}

    for pkg in packages:
        duration = pkg.get("duration")
        base = pkg.get("base_price", 0)
        offer = pkg.get("offer_price", 0)
        if duration:
            result[duration] = f"₹{base:,} (Offer Price: ₹{offer:,})"

    if not result and course_id == "yoga":
        return {
            "1 Month": "₹700 (Offer Price: ₹300)",
            "3 Months": "₹1,750 (Offer Price: ₹600)",
            "6 Months": "₹3,200 (Offer Price: ₹1,000)",
            "1 Year": "₹5,000 (Offer Price: ₹1,800)",
        }
    return result


def get_course_coupons(course_id: str = "yoga") -> Dict[str, Dict[str, Any]]:
    """
    Dynamically extracts coupons for each package duration from the course JSON.
    Zero hardcoded values: pulls duration, base_price, offer_price, and coupon_code directly from JSON.
    """
    data = get_course_data(course_id)
    packages = data.get("pricing", {}).get("packages", [])
    coupons = {}
    cid_clean = (course_id or "yoga").lower().strip()
    cid_upper = cid_clean.upper()

    for pkg in packages:
        duration = pkg.get("duration")
        if not duration:
            continue
        base = pkg.get("base_price", 0)
        offer = pkg.get("offer_price", 0)
        code = pkg.get("coupon_code") or f"{cid_upper}{offer}"

        norm = duration.lower()
        aliases = [norm, norm.replace(" ", "")]
        if "month" in norm:
            num = norm.split()[0]
            aliases.extend([f"{num}m", f"{num} month", f"{num} months", f"{num} mahina", f"{num} mahine"])
        elif "year" in norm:
            aliases.extend(["1y", "1yr", "yearly", "annual", "1 saal", "12 months"])
        aliases.append(code.lower())

        coupons[duration] = {
            "code": code,
            "base_price": base,
            "offer_price": offer,
            "currency": "₹",
            "duration": duration,
            "aliases": aliases,
        }

    return coupons


def _normalize_consonants(word: str) -> str:
    """Extracts consonant skeleton from a word (e.g. 'kthk' -> 'kthk', 'kathak' -> 'kthk')."""
    return re.sub(r"[^bcdfghjklmnpqrstvwxyz]", "", word.lower())


def detect_course_from_text(text: str, default_course: Optional[str] = "yoga") -> Optional[str]:
    """
    Robustly identifies if a user is asking about a specific course from the catalog.
    Handles:
      1. Exact keyword & alias matching ('kathak', 'classical dance', 'guitar')
      2. Consonant skeleton matching for abbreviations ('kthk' -> 'kathak', 'yga' -> 'yoga', 'zmba' -> 'zumba')
      3. Fuzzy ratio >= 75 for phonetic typos ('katak', 'danc', 'gitar')
    If no course is mentioned in the text, returns default_course.
    """
    if not text:
        return default_course

    q = text.lower().strip()
    words = re.findall(r"\w+", q)
    catalog = get_course_catalog()

    from rapidfuzz import fuzz

    for item in catalog:
        cid = item.get("id")
        aliases = item.get("aliases", []) + [cid]
        for alias in aliases:
            # Check full alias phrase match first (e.g. 'classical dance', 'spoken english')
            if " " in alias and alias in q:
                return cid

            alias_cons = _normalize_consonants(alias)
            for w in words:
                # 1. Exact token match
                if w == alias:
                    return cid

                # 2. Consonant skeleton match (e.g. 'kthk' -> 'kathak')
                w_cons = _normalize_consonants(w)
                if len(w_cons) >= 3 and w_cons == alias_cons:
                    return cid

                # 3. Fuzzy similarity for typos (e.g. 'katak' -> 'kathak')
                if len(w) >= 3 and len(alias) >= 3 and fuzz.ratio(w, alias) >= 75:
                    return cid

    return default_course


def format_batch_timings_display(course_id: str = "yoga") -> str:
    """
    Dynamically groups batch timings by slot (Morning, Afternoon, Evening, Weekend)
    from the structured course JSON for display in chat messages.
    """
    data = get_course_data(course_id)
    batches = data.get("batch_timings", {}).get("batches", [])
    slots = {}
    for b in batches:
        slot_name = b.get("slot", "Available").strip().title()
        time_str = b.get("time_12h", "")
        if time_str:
            slots.setdefault(slot_name, []).append(time_str)

    lines = []
    for slot, times in slots.items():
        lines.append(f"• {slot}: " + ", ".join(times))

    if not lines:
        return "• Mon to Fri: Flexible batch timings available in app/website."
    return "\n".join(lines)


def format_package_fees_display(course_id: str = "yoga") -> str:
    """
    Dynamically formats package duration and fee bullet points for any course.
    """
    packages = get_valid_packages(course_id)
    lines = [f"• {pkg}: {fee}" for pkg, fee in packages.items()]
    return "\n".join(lines)


def format_demo_videos_display(course_id: str = "yoga") -> str:
    """
    Dynamically formats available demo video links from course JSON.
    """
    data = get_course_data(course_id)
    demos = data.get("trial_class", {}).get("demo_videos", {})
    lines = [f"• Trainer {name}: {url}" for name, url in demos.items() if url]
    return "\n".join(lines)
