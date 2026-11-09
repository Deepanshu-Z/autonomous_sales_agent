"""
schedule_engine.py — Batch Timings, Teacher Rosters & Schedule Queries.

Single-responsibility component for querying, classifying, and formatting
all batch timings, teacher assignments, and time slots across courses.
Zero hardcoding: all data is resolved dynamically from course knowledge bases.
"""

import re
from typing import Dict, Any, List, Optional


def _clock_value(value: str) -> Optional[int]:
    """Return a comparable 24-hour minute value from a catalog time token."""
    if not value:
        return None
    token = value.strip().upper().replace(".", ":")
    match = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(AM|PM)?", token)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = match.group(3)
    if meridiem:
        if not 1 <= hour <= 12 or minute > 59:
            return None
        if meridiem == "AM" and hour == 12:
            hour = 0
        elif meridiem == "PM" and hour != 12:
            hour += 12
    elif hour > 23 or minute > 59:
        return None
    return hour * 60 + minute


def _batch_bounds(batch: Dict[str, Any]) -> Optional[tuple[int, int]]:
    """Read bounds from catalog data; never infer them from a neighboring slot."""
    raw = str(batch.get("time_24h") or "")
    parts = re.split(r"\s*[-–—]\s*", raw, maxsplit=1)
    if len(parts) == 2:
        start, end = (_clock_value(part) for part in parts)
        if start is not None and end is not None:
            return start, end

    raw = str(batch.get("time_12h") or "")
    parts = re.split(r"\s+(?:to|[-–—])\s+", raw, maxsplit=1, flags=re.IGNORECASE)
    if len(parts) == 2:
        start, end = (_clock_value(part) for part in parts)
        if start is not None and end is not None:
            return start, end
    return None


def resolve_requested_batch(
    text: str,
    course_id: Optional[str],
    llm_timing: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolve a customer's timing against the active catalog.

    The LLM timing is only a fallback for number-word expressions. Numeric
    ranges/hours and periods from the raw message always win. No batch is
    invented or snapped to a nearby catalog entry.
    """
    result = {
        "status": "NONE",
        "timing": None,
        "candidates": [],
        "period": None,
        "meridiem": None,
        "requested_text": text or "",
    }
    if not text or not course_id:
        return result

    lowered = text.casefold()
    period_words = {
        "Morning": ("morning", "subah", "subha", "sawere", "savere", "mrng", "am"),
        "Afternoon": ("afternoon", "dopahar", "dupehar", "lunch"),
        "Evening": ("evening", "shaam", "sham", "raat", "night", "pm"),
    }
    words = set(re.findall(r"[a-z]+", lowered))
    if re.search(r"(?<![a-z])(?:am|pm)\b", lowered):
        words.update(re.findall(r"(?<![a-z])(?:am|pm)\b", lowered))
    mentioned_periods = [
        period for period, aliases in period_words.items()
        if any(alias in words for alias in aliases if alias not in ("am", "pm"))
    ]
    if "am" in words:
        result["meridiem"] = "AM"
    elif "pm" in words:
        result["meridiem"] = "PM"
    if len(set(mentioned_periods)) == 1:
        result["period"] = mentioned_periods[0]
    elif len(set(mentioned_periods)) > 1:
        result["status"] = "AMBIGUOUS"
        result["reason"] = "CONFLICTING_PERIODS"
        return result

    # Generic numeric extraction. The catalog, not this parser, defines valid hours.
    raw_numbers = re.findall(r"(?<!\d)\d{1,2}(?::\d{2})?", lowered)
    numbers = [int(value.split(":", 1)[0]) for value in raw_numbers]
    if not result["period"] and any(13 <= value <= 23 for value in numbers):
        result["period"] = "Afternoon" if any(13 <= value < 16 for value in numbers) else "Evening"
        result["meridiem"] = "PM"
    elif result["period"] and not result["meridiem"]:
        result["meridiem"] = "AM" if result["period"] == "Morning" else "PM"
    has_range_word = any(token in lowered for token in ("-", "–", "—", " to ", " se ", "to"))
    has_clock_word = any(token in lowered for token in ("baje", "bje", "o'clock", "clock"))
    has_time_signal = bool(numbers and (result["period"] or has_range_word or has_clock_word))

    batches = get_available_timings(course_id)
    catalog = []
    for batch in batches:
        bounds = _batch_bounds(batch)
        if bounds:
            catalog.append((batch, bounds))

    def period_matches(batch: Dict[str, Any]) -> bool:
        if result["period"] and (batch.get("slot") or "").casefold() != result["period"].casefold():
            return False
        if result["meridiem"]:
            bounds = _batch_bounds(batch)
            if not bounds:
                return False
            batch_meridiem = "AM" if bounds[0] < 12 * 60 else "PM"
            if batch_meridiem != result["meridiem"]:
                return False
        return True

    candidates = []
    if len(numbers) >= 2 and has_range_word:
        start_hour, end_hour = numbers[0], numbers[1]
        # An explicit meridiem/period applies to the requested range.
        requested_start = start_hour * 60
        requested_end = end_hour * 60
        candidates = [
            batch for batch, (start, end) in catalog
            if start % 60 == requested_start % 60
            and end % 60 == requested_end % 60
            and start // 60 % 12 == start_hour % 12
            and end // 60 % 12 == end_hour % 12
            and period_matches(batch)
        ]
    elif numbers and (result["period"] or has_clock_word):
        requested_hour = numbers[0]
        candidates = [
            batch for batch, (start, _) in catalog
            if start // 60 % 12 == requested_hour % 12 and period_matches(batch)
        ]

    if candidates:
        result["candidates"] = [batch.get("time_12h") for batch in candidates]
        if len(candidates) == 1:
            result["status"] = "SELECTED"
            result["timing"] = candidates[0].get("time_12h")
        else:
            result["status"] = "AMBIGUOUS"
        return result

    if has_time_signal:
        result["status"] = "UNAVAILABLE"
        result["reason"] = "NO_EXACT_CATALOG_BATCH"
        return result

    if result["period"] and not numbers:
        period_candidates = [
            batch.get("time_12h") for batch, _ in catalog if period_matches(batch)
        ]
        result["candidates"] = period_candidates
        result["status"] = "PERIOD_ONLY"
        return result

    # For number words, use the extractor only as a candidate, then validate it
    # against the catalog and the raw period. It can never override an explicit
    # numeric range/hour from the customer message.
    if llm_timing:
        for batch, _ in catalog:
            if batch.get("time_12h") == llm_timing and period_matches(batch):
                result["status"] = "SELECTED"
                result["timing"] = llm_timing
                result["candidates"] = [llm_timing]
                return result
    return result


def resolve_catalog_batch_semantics(
    course_id: Optional[str],
    start_24h: Optional[str] = None,
    end_24h: Optional[str] = None,
    period: Optional[str] = None,
    raw_text: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolve normalized semantic timing fields against catalog data.

    This is the authoritative path: it does not parse customer text, use
    aliases, or contain schedule values. The semantic extractor supplies the
    normalized interval; this function only validates exact catalog matches.
    """
    result = {"status": "NONE", "timing": None, "candidates": []}
    if not course_id:
        return result

    def parse_clock(value: Optional[str]) -> Optional[int]:
        if not isinstance(value, str):
            return None
        tokens = value.strip().upper().split()
        meridiem = tokens[-1] if tokens and tokens[-1] in {"AM", "PM"} else None
        clock = tokens[0] if tokens else ""
        parts = clock.split(":")
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            if len(parts) == 1 and parts[0].isdigit():
                hour, minute = int(parts[0]), 0
            else:
                return None
        else:
            hour, minute = (int(part) for part in parts)
        if meridiem:
            if hour < 1 or hour > 12 or minute > 59:
                return None
            if meridiem == "AM" and hour == 12:
                hour = 0
            elif meridiem == "PM" and hour != 12:
                hour += 12
        elif hour > 23 or minute > 59:
            return None
        return hour * 60 + minute

    def catalog_bounds(batch: Dict[str, Any]) -> Optional[tuple[int, int]]:
        raw = str(batch.get("time_24h") or "")
        parts = raw.split("-", 1)
        if len(parts) != 2:
            return None
        start, end = (parse_clock(part) for part in parts)
        return (start, end) if start is not None and end is not None else None

    batches = get_available_timings(course_id)

    requested_start = parse_clock(start_24h)
    requested_end = parse_clock(end_24h)

    def resolve_raw_requests(text: str) -> Dict[str, Any]:
        """Resolve all timing expressions in free text against the catalog.

        The catalog remains the authority; this only turns common WhatsApp
        shorthand into candidate intervals. It deliberately returns multiple
        candidates instead of silently choosing the first or last number.
        """
        result = {"status": "NONE", "timing": None, "candidates": [], "invalid": [], "skip": False}
        raw = str(text or "").strip().lower()
        if not raw:
            return result

        # Package phrases own their numbers ("1 month ka dedo"). Do not let
        # the timing parser reinterpret those numbers as hours.
        package_words = r"month|months|year|years|mahina|mahine|saal"
        if re.search(rf"\b(?:\d{{1,2}}\s*)?(?:{package_words})\b", raw, re.IGNORECASE):
            result["skip"] = True
            return result

        if re.search(r"\b(?:baar|bar|times|weeks?|hafte?)\b", raw, re.IGNORECASE):
            result["skip"] = True
            return result

        period = None
        if re.search(r"\b(?:morning|subah|subha|sawere|savere|mrng|sbh)\b", raw):
            period = "Morning"
        elif re.search(r"\b(?:evening|shaam|sham|raat|night)\b", raw):
            period = "Evening"
        elif re.search(r"\b(?:afternoon|dopahar|lunch)\b", raw):
            period = "Afternoon"

        def to_minutes(hour: int, minute: int, meridiem: Optional[str]) -> Optional[int]:
            if minute > 59:
                return None
            if meridiem:
                if hour < 1 or hour > 12:
                    return None
                if meridiem == "AM" and hour == 12:
                    hour = 0
                elif meridiem == "PM" and hour != 12:
                    hour += 12
            elif hour > 23:
                return None
            return hour * 60 + minute

        def bounds(batch: Dict[str, Any]) -> Optional[tuple[int, int]]:
            for key, separator in (("time_24h", r"\s*[-\u2013\u2014]\s*"), ("time_12h", r"\s*(?:to|[-\u2013\u2014])\s*")):
                raw_batch_time = str(batch.get(key) or "")
                parts = re.split(separator, raw_batch_time, maxsplit=1, flags=re.IGNORECASE)
                if len(parts) == 2:
                    start, end = parse_clock(parts[0]), parse_clock(parts[1])
                    if start is not None and end is not None:
                        return start, end
            return None

        def match_interval(start: int, end: Optional[int]) -> List[str]:
            matches = []
            for batch in batches:
                b = bounds(batch)
                if not b:
                    continue
                if period:
                    slot_val = str(batch.get("slot") or "").strip().casefold()
                    batch_period = (classify_time_period(str(batch.get("time_12h") or batch.get("time_24h") or "")) or "").casefold()
                    if slot_val in {"morning", "afternoon", "evening"}:
                        if slot_val != period.casefold():
                            continue
                    elif batch_period:
                        if batch_period != period.casefold():
                            continue
                if b[0] == start and (end is None or b[1] == end):
                    matches.append(batch.get("time_12h"))
            return [m for m in matches if m]

        requests: List[tuple[int, Optional[int], bool]] = []
        invalid_requests: List[str] = []
        spans = []
        range_pattern = re.compile(
            r"(?<!\d)(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*(?:to|-|\u2013|\u2014|se)\s*"
            r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?",
            re.IGNORECASE,
        )
        for match in range_pattern.finditer(raw):
            start_meridiem = (match.group(3) or match.group(6) or "").upper() or None
            end_meridiem = (match.group(6) or match.group(3) or "").upper() or None
            start = to_minutes(int(match.group(1)), int(match.group(2) or 0), start_meridiem)
            end = to_minutes(int(match.group(4)), int(match.group(5) or 0), end_meridiem)
            if start is not None and end is not None:
                if not start_meridiem and not end_meridiem and end <= start and end < 12 * 60:
                    end += 12 * 60
                requests.append((start, end, bool(start_meridiem or end_meridiem)))
                spans.append(match.span())

        # WhatsApp shorthand: "10 11" means an interval when the two numbers
        # are adjacent. Alternatives remain separate requests, e.g.
        # "10 11 or 56" => [10-11, 56].
        token_matches = list(re.finditer(r"(?<!\d)(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", raw, re.IGNORECASE))
        uncovered_tokens = [
            token for token in token_matches
            if not any(start <= token.start() and token.end() <= end for start, end in spans)
        ]
        if uncovered_tokens:
            index = 0
            while index < len(uncovered_tokens):
                current = uncovered_tokens[index]
                next_match = uncovered_tokens[index + 1] if index + 1 < len(uncovered_tokens) else None
                between = raw[current.end():next_match.start()].strip() if next_match else ""
                if next_match and not between:
                    second_meridiem = (next_match.group(3) or current.group(3) or "").upper() or None
                    first_meridiem = (current.group(3) or second_meridiem or "").upper() or None
                    start = to_minutes(int(current.group(1)), int(current.group(2) or 0), first_meridiem)
                    end = to_minutes(int(next_match.group(1)), int(next_match.group(2) or 0), second_meridiem)
                    if start is not None and end is not None:
                        requests.append((start, end, bool(first_meridiem or second_meridiem)))
                    else:
                        invalid_requests.append(raw[current.start():next_match.end()].strip())
                    index += 2
                    continue

                meridiem = (current.group(3) or "").upper() or None
                hour = int(current.group(1))
                minute = int(current.group(2) or 0)
                if not meridiem and period == "Morning":
                    meridiem = "AM"
                elif not meridiem and period in {"Afternoon", "Evening"}:
                    meridiem = "PM"
                start = to_minutes(hour, minute, meridiem)
                if start is not None:
                    requests.append((start, None, bool(meridiem)))
                else:
                    invalid_requests.append(current.group(0).strip())
                index += 1

        # A single hour is valid only when the catalog resolves it uniquely.
        if not requests:
            token_matches = list(re.finditer(r"(?<!\d)(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", raw, re.IGNORECASE))
            if len(token_matches) == 1:
                token = token_matches[0]
                meridiem = (token.group(3) or "").upper() or None
                hour = int(token.group(1))
                minute = int(token.group(2) or 0)
                if not meridiem and period == "Morning":
                    meridiem = "AM"
                elif not meridiem and period in {"Afternoon", "Evening"}:
                    meridiem = "PM"
                start = to_minutes(hour, minute, meridiem)
                if start is not None:
                    requests.append((start, None, bool(meridiem)))

        if not requests:
            if invalid_requests:
                return {"status": "UNAVAILABLE", "timing": None, "candidates": [], "invalid": invalid_requests}
            return result

        valid: List[str] = []
        invalid: List[str] = list(invalid_requests)
        for start, end, explicit_meridiem in requests:
            matches = match_interval(start, end)
            if not explicit_meridiem and start < 12 * 60:
                # Bare 12-hour input is matched against the catalog's actual
                # AM/PM values; no schedule value is invented or hardcoded.
                matches = list(dict.fromkeys(matches + match_interval(start + 12 * 60, end + 12 * 60 if end is not None and end < 12 * 60 else end)))
            if matches:
                valid.extend(matches)
            else:
                if end is not None:
                    invalid.append(f"{start // 60:02d}:{start % 60:02d}-{end // 60:02d}:{end % 60:02d}")
                else:
                    invalid.append(f"{start // 60:02d}:{start % 60:02d}")
        valid = list(dict.fromkeys(valid))

        if len(valid) == 1 and not invalid:
            return {"status": "SELECTED", "timing": valid[0], "candidates": valid, "invalid": []}
        if len(valid) > 1 and not invalid:
            return {"status": "MULTIPLE_VALID", "timing": None, "candidates": valid, "invalid": []}
        if valid:
            return {"status": "MIXED", "timing": None, "candidates": valid, "invalid": invalid}
        return {"status": "UNAVAILABLE", "timing": None, "candidates": [], "invalid": invalid}

    if raw_text:
        raw_resolution = resolve_raw_requests(raw_text)
        if raw_resolution["status"] != "NONE" or raw_resolution.get("skip"):
            return raw_resolution

    # Detect period if not provided
    if not period and raw_text:
        low = raw_text.lower()
        if re.search(r'\b(evening|shaam|sham|raat|night)\b', low):
            period = "Evening"
        elif re.search(r'\b(afternoon|dopahar|lunch)\b', low):
            period = "Afternoon"
        elif re.search(r'\b(morning|subah|subha|sawere|savere|mrng|sbh)\b', low):
            period = "Morning"

    # Raw numeric evidence outranks an extractor's rewritten canonical slot.
    # Respects explicit meridiem (am/pm), multi-number contexts, and colloquial periods.
    if raw_text:
        tokens = []
        for m in re.finditer(r'(?<!\d)(\d{1,2})(?::(\d{2}))?\s*(am|pm)?', raw_text, re.IGNORECASE):
            hr = int(m.group(1))
            mn = int(m.group(2) or 0)
            merid = m.group(3).upper() if m.group(3) else None
            tokens.append((hr, mn, merid, m.start()))

        if not tokens:
            requested_start = None
            requested_end = None
        else:
            # If multiple tokens and some have explicit meridiem, prefer token with meridiem
            merid_tokens = [t for t in tokens if t[2]]
            chosen = merid_tokens[-1] if merid_tokens else tokens[-1]

            # Range match: e.g. "12 to 1", "5-6"
            range_match = re.search(r'(?<!\d)(\d{1,2})(?::(\d{2}))?\s*(?:to|-|se)\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?', raw_text, re.IGNORECASE)
            if range_match:
                s_hr = int(range_match.group(1))
                s_mn = int(range_match.group(2) or 0)
                e_hr = int(range_match.group(3))
                e_mn = int(range_match.group(4) or 0)
                r_merid = range_match.group(5).upper() if range_match.group(5) else (chosen[2] or (
                    "PM" if period in ("Evening", "Afternoon") else ("AM" if period == "Morning" else None)
                ))
                if r_merid == "PM":
                    s_hr = s_hr if s_hr == 12 else (s_hr + 12 if s_hr < 12 else s_hr)
                    e_hr = e_hr if e_hr == 12 else (e_hr + 12 if e_hr < 12 else e_hr)
                elif r_merid == "AM":
                    s_hr = 0 if s_hr == 12 else s_hr
                    e_hr = 0 if e_hr == 12 else e_hr
                requested_start = s_hr * 60 + s_mn
                requested_end = e_hr * 60 + e_mn
            else:
                hr, mn, merid, _ = chosen
                effective_merid = merid or ("PM" if period in ("Evening", "Afternoon") else ("AM" if period == "Morning" else None))
                if effective_merid == "PM":
                    hr = hr if hr == 12 else (hr + 12 if hr < 12 else hr)
                elif effective_merid == "AM":
                    hr = 0 if hr == 12 else hr
                elif period == "Evening" and hr < 12:
                    hr += 12
                elif period == "Afternoon" and 1 <= hr <= 6:
                    hr += 12

                # If bare hour without meridiem or period, evaluate ambiguity across AM and PM
                if not effective_merid and not period and hr <= 12:
                    am_time = (0 if hr == 12 else hr) * 60 + mn
                    pm_time = (12 if hr == 12 else hr + 12) * 60 + mn
                    am_cands = [b for b in batches if (bounds := catalog_bounds(b)) and bounds[0] == am_time]
                    pm_cands = [b for b in batches if (bounds := catalog_bounds(b)) and bounds[0] == pm_time]
                    if am_cands and pm_cands:
                        result["status"] = "AMBIGUOUS"
                        result["candidates"] = [b.get("time_12h") for b in am_cands + pm_cands]
                        return result
                    elif len(am_cands) == 1 and not pm_cands:
                        result["status"] = "SELECTED"
                        result["timing"] = am_cands[0].get("time_12h")
                        result["candidates"] = [result["timing"]]
                        return result
                    elif len(pm_cands) == 1 and not am_cands:
                        result["status"] = "SELECTED"
                        result["timing"] = pm_cands[0].get("time_12h")
                        result["candidates"] = [result["timing"]]
                        return result
                    else:
                        result["status"] = "UNAVAILABLE"
                        return result
                requested_start = hr * 60 + mn

    def matches(batch: Dict[str, Any], bounds: tuple[int, int]) -> bool:
        if period and str(batch.get("slot") or "").casefold() != str(period).casefold():
            return False
        if requested_start is not None and bounds[0] != requested_start:
            return False
        if requested_end is not None and bounds[1] != requested_end:
            return False
        return True

    candidates = [
        batch for batch in batches
        if (bounds := catalog_bounds(batch)) is not None and matches(batch, bounds)
    ]
    result["candidates"] = [batch.get("time_12h") for batch in candidates]

    if requested_start is None and requested_end is None and not period:
        result["status"] = "NONE"
        result["candidates"] = []
        return result

    if len(candidates) == 1 and (requested_start is not None or not period):
        result["status"] = "SELECTED"
        result["timing"] = candidates[0].get("time_12h")
        return result
    if len(candidates) >= 1 and period and requested_start is None and requested_end is None:
        result["status"] = "PERIOD_ONLY"
        return result
    if len(candidates) > 1:
        result["status"] = "AMBIGUOUS"
        return result

    if requested_start is not None or requested_end is not None:
        result["status"] = "UNAVAILABLE"
        return result

    if period:
        result["status"] = "PERIOD_ONLY"
        return result

    # Missing semantic timing data is deliberately unresolved. A canonical
    # timing guessed by the extractor is never enough to select a batch.
    result["status"] = "NONE"
    return result


def classify_time_period(time_str: str) -> Optional[str]:
    """Dynamically categorizes any timing string into 'Morning', 'Afternoon', or 'Evening'.
    - Morning (AM): 4:00 AM – 11:59 AM
    - Afternoon (PM): 12:00 PM – 3:59 PM
    - Evening (PM): 4:00 PM – 11:59 PM
    """
    if not time_str:
        return None
    m = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(AM|PM)", time_str, re.IGNORECASE)
    if not m:
        m24 = re.search(r"(\d{1,2}):(\d{2})", time_str)
        if m24:
            hr = int(m24.group(1))
            if 4 <= hr < 12:
                return "Morning"
            elif 12 <= hr < 16:
                return "Afternoon"
            elif 16 <= hr <= 23:
                return "Evening"
        return None
    hr = int(m.group(1))
    merid = m.group(3).upper()
    if merid == "AM":
        return "Morning"
    else:
        if hr == 12 or 1 <= hr < 4:
            return "Afternoon"
        return "Evening"


def get_available_timings(course_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Returns the standardized list of batch timings for any course.
    Each item contains human-readable keys: 'slot', 'days', 'time_12h', 'time_24h', 'instructors'.
    Dynamic, course-agnostic, reads directly from course KB.
    """
    if not course_id:
        return []
    from src.core.kb_loader import get_course_data
    course_data = get_course_data(course_id)
    batches = course_data.get("batch_timings", {}).get("batches", [])
    if not batches:
        return []

    available_timings = []
    for batch in batches:
        time_12h = batch.get("time_12h", "").strip()
        days = batch.get("days", "").strip() or batch.get("slot", "").strip()
        instructors = [str(inst).strip() for inst in batch.get("instructors", []) if str(inst).strip()]
        available_timings.append({
            "slot": batch.get("slot", "").strip(),
            "days": days,
            "time_12h": time_12h,
            "time_24h": batch.get("time_24h", "").strip(),
            "instructors": instructors,
        })
    return available_timings


def get_valid_timings(course_id: Optional[str] = None) -> List[str]:
    """Dynamically extracts formatted valid batch timing strings (e.g. '5:00–6:00 PM')
    straight from the course JSON — never a hand-typed list.
    """
    if not course_id:
        return []
    available = get_available_timings(course_id)
    valid_timings = []
    for b in available:
        t = b.get("time_12h", "")
        m = re.match(r"(\d{1,2}:\d{2}\s*(?:AM|PM))\s*(?:to|-|–)\s*(\d{1,2}:\d{2}\s*(?:AM|PM))", t, re.IGNORECASE)
        if m:
            start, end = m.group(1).strip(), m.group(2).strip()
            sp, ep = start.split(), end.split()
            canonical = f"{sp[0]}–{ep[0]} {ep[1].upper()}" if sp[1].upper() == ep[1].upper() else f"{start}–{end}"
            valid_timings.append(canonical)
        elif t:
            valid_timings.append(t)
    # Deduplicate while preserving order
    seen = set()
    deduped = []
    for vt in valid_timings:
        if vt not in seen:
            seen.add(vt)
            deduped.append(vt)
    return deduped


def get_teachers_for_slot(course_id: Optional[str] = None, slot_or_timing: str = "") -> List[str]:
    """Returns a deduplicated list of instructor names assigned to a given timing or slot.
    Handles flexible queries like '5:00 PM', '5-6 PM', '5 pm', 'Evening', or 'Tuesday/Thursday'.
    Dynamic and reusable across all courses.
    """
    if not course_id or not slot_or_timing:
        return []

    query = slot_or_timing.lower().strip()
    cleaned_query = re.sub(r"[\s\-_–:]+", " ", query)
    q_hr, q_merid = extract_time_tokens(slot_or_timing)

    available_batches = get_available_timings(course_id)
    matched_teachers: List[str] = []

    for batch in available_batches:
        time_str = batch.get("time_12h", "").lower()
        days_str = batch.get("days", "").lower()
        slot_str = batch.get("slot", "").lower()
        cleaned_time = re.sub(r"[\s\-_–:]+", " ", time_str)

        is_match = False
        if q_hr is not None and q_merid is not None:
            b_hr, b_merid = extract_time_tokens(time_str)
            if b_hr == q_hr and b_merid == q_merid:
                is_match = True
        elif query in time_str or query == slot_str or query in days_str:
            is_match = True
        elif cleaned_query in cleaned_time:
            is_match = True

        if is_match:
            for instructor in batch.get("instructors", []):
                if instructor and instructor not in matched_teachers:
                    matched_teachers.append(instructor)

    return matched_teachers


def get_slots_for_teacher(course_id: Optional[str] = None, teacher_name: str = "") -> List[Dict[str, Any]]:
    """Returns all batch timings taught by a specific teacher for the given course.
    Matches teacher names flexibly (e.g. 'madhuri' matches 'Ms. Madhuri').
    """
    if not course_id or not teacher_name:
        return []

    target_name = teacher_name.lower().strip()
    available_batches = get_available_timings(course_id)
    matching_slots = []

    for batch in available_batches:
        for instructor in batch.get("instructors", []):
            inst_lower = instructor.lower()
            if target_name in inst_lower or inst_lower in target_name:
                matching_slots.append({
                    "time_12h": batch.get("time_12h", ""),
                    "days": batch.get("days", ""),
                    "slot": batch.get("slot", ""),
                    "instructor": instructor,
                })
                break

    return matching_slots


def resolve_teacher_timing_constraints(
    course_id: Optional[str] = None,
    teacher: Optional[str] = None,
    timing: Optional[str] = None,
    period: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Dynamically analyzes the intersection between Teacher, Timing, and Period for any course.
    Catalog-driven, course-agnostic, zero hardcoding.
    """
    result = {
        "auto_assigned_timing": None,
        "auto_assigned_teacher": None,
        "available_timings_for_teacher": [],
        "available_teachers_for_timing": [],
        "is_conflict": False,
        "constraint_note": None,
    }
    if not course_id:
        return result

    cid = course_id.lower().strip()
    from src.core.helpers.schedule_engine import resolve_canonical_teacher, classify_time_period
    norm_teacher = resolve_canonical_teacher(teacher, course_id=cid) if teacher else None
    active_teacher = norm_teacher or teacher

    # 1. Resolve by Teacher
    if active_teacher:
        t_slots = get_slots_for_teacher(cid, active_teacher)
        all_t_timings = [s.get("time_12h") for s in t_slots if s.get("time_12h")]
        result["available_timings_for_teacher"] = all_t_timings

        # Conflict check if timing is already known
        if timing:
            from src.core.helpers.schedule_engine import extract_time_tokens
            c_hr, c_merid = extract_time_tokens(timing)
            teaches_it = any(extract_time_tokens(t) == (c_hr, c_merid) for t in all_t_timings if c_hr is not None)
            if not teaches_it:
                result["is_conflict"] = True
                result["constraint_note"] = (
                    f"{active_teacher} does not teach at {timing}. "
                    f"Her available timings are: {', '.join(all_t_timings)}."
                )
                return result

        # If timing is NOT known, check if period or single slot resolves it
        if not timing:
            norm_period = (period or "").strip().title()
            if norm_period:
                p_slots = [
                    s.get("time_12h") for s in t_slots
                    if classify_time_period(s.get("time_12h")) == norm_period or norm_period.lower() in (s.get("slot") or "").lower()
                ]
                if len(p_slots) == 1:
                    result["auto_assigned_timing"] = p_slots[0]
                    result["constraint_note"] = (
                        f"According to your chosen instructor ({active_teacher}) and preferred {norm_period.lower()} timing, "
                        f"{active_teacher} conducts classes at {p_slots[0]}, which has been assigned for you."
                    )
                elif len(p_slots) > 1:
                    result["available_timings_for_teacher"] = p_slots
                    result["constraint_note"] = (
                        f"For your selected instructor ({active_teacher}) in the {norm_period.lower()}, "
                        f"available batches are: {', '.join(p_slots)}."
                    )
                elif len(p_slots) == 0:
                    result["constraint_note"] = (
                        f"{active_teacher} does not teach in the {norm_period.lower()}. "
                        f"Her available timings are: {', '.join(all_t_timings)}."
                    )
            elif len(all_t_timings) == 1:
                result["auto_assigned_timing"] = all_t_timings[0]
                result["constraint_note"] = (
                    f"Instructor {active_teacher} conducts classes exclusively at {all_t_timings[0]}, "
                    f"so this batch has been assigned for you."
                )

    # 2. Resolve by Timing
    if timing and not active_teacher:
        slot_teachers = get_teachers_for_slot(cid, timing)
        result["available_teachers_for_timing"] = slot_teachers
        if len(slot_teachers) == 1:
            result["auto_assigned_teacher"] = slot_teachers[0]
            result["constraint_note"] = (
                f"For the {timing} batch, your classes are conducted by {slot_teachers[0]}."
            )

    return result


def get_course_time_periods(course_id: Optional[str] = None) -> Dict[str, Any]:
    """Dynamically parses and categorizes all batches for a course into time periods:
    'Morning', 'Afternoon', and 'Evening'. Zero hardcoding.
    """
    if not course_id:
        return {
            "has_timings": False,
            "available_periods": [],
            "missing_periods": ["Morning", "Afternoon", "Evening"],
            "batches_by_period": {},
            "summary": "Check official app/website for schedule.",
        }
    from src.core.kb_loader import get_course_data
    data = get_course_data(course_id)
    batches = data.get("batch_timings", {}).get("batches", [])
    if not batches:
        return {
            "has_timings": False,
            "available_periods": [],
            "missing_periods": ["Morning", "Afternoon", "Evening"],
            "batches_by_period": {},
            "summary": "Live batch schedules are available on the Sensationz App and website.",
        }

    batches_by_period: Dict[str, List[str]] = {"Morning": [], "Afternoon": [], "Evening": []}
    for b in batches:
        t = b.get("time_12h", "") or b.get("time_24h", "")
        period = classify_time_period(t)
        if not period and b.get("slot") in ("Morning", "Afternoon", "Evening"):
            period = b.get("slot")
        if period and period in batches_by_period:
            if t and t not in batches_by_period[period]:
                batches_by_period[period].append(t)

    available = [p for p in ["Morning", "Afternoon", "Evening"] if batches_by_period[p]]
    missing = [p for p in ["Morning", "Afternoon", "Evening"] if not batches_by_period[p]]

    course_name = data.get("course_overview", {}).get("name", course_id.title())
    if len(available) == 1:
        only_p = available[0]
        missing_str = " or ".join(missing)
        summary = (
            f"Batches are held in the {only_p} only ({', '.join(batches_by_period[only_p])}). "
            f"CRITICAL: {course_name} has NO {missing_str} batches!"
        )
    elif available:
        descs = [f"{p} ({', '.join(batches_by_period[p])})" for p in available]
        missing_str = f" (NO {' or '.join(missing)} batches)" if missing else ""
        summary = f"Batches available: {'; '.join(descs)}{missing_str}."
    else:
        summary = "Check the Sensationz App or website for live batch schedules."

    return {
        "has_timings": True,
        "available_periods": available,
        "missing_periods": missing,
        "batches_by_period": batches_by_period,
        "summary": summary,
    }


def format_batch_timings_display(course_id: Optional[str] = None, teacher: Optional[str] = None) -> str:
    """Formats available batches cleanly for WhatsApp messages and system prompt display.
    If a teacher is provided, filters to ONLY the batches taught by that specific teacher."""
    if not course_id:
        return ""
    from src.core.kb_loader import get_course_data
    data = get_course_data(course_id)
    batches = data.get("batch_timings", {}).get("batches", [])
    
    # Filter by teacher if specified and not 'NOT SELECTED'
    if teacher and str(teacher).strip().upper() not in ("NOT SELECTED", "NONE", ""):
        target_t = str(teacher).lower().strip()
        filtered = []
        for b in batches:
            for inst in b.get("instructors", []):
                inst_low = inst.lower()
                if target_t in inst_low or inst_low in target_t:
                    filtered.append(b)
                    break
        if filtered:
            batches = filtered

    slots: Dict[str, List[str]] = {}
    for b in batches:
        slot_name = b.get("slot") or b.get("days") or "Available"
        slot_name = slot_name.strip()
        t = b.get("time_12h", "")
        if t and t not in slots.setdefault(slot_name, []):
            slots[slot_name].append(t)
    if not slots:
        return "• Flexible batch timings available — check the app/website for the current schedule."
    lines = []
    for slot, times in slots.items():
        formatted_slot = slot.replace("/", " & ")
        lines.append(f"• *{formatted_slot}:* " + ", ".join(times))
    return "\n".join(lines)


def format_teacher_batch_schedule(course_id: Optional[str] = None) -> str:
    """Pre-computes a human-readable teacher -> batch timings mapping from course JSON.
    Injected into the system prompt so the LLM doesn't need to traverse minified JSON
    to answer teacher-specific timing queries — eliminates teacher-timing hallucinations.
    """
    if not course_id:
        return ""
    from src.core.kb_loader import get_course_data
    data = get_course_data(course_id)
    batches = data.get("batch_timings", {}).get("batches", [])
    if not batches:
        return ""

    teacher_map: Dict[str, List[str]] = {}
    for b in batches:
        days = b.get("days") or b.get("slot") or ""
        time_str = b.get("time_12h", "")
        if not days or not time_str:
            continue
        for instructor in b.get("instructors", []):
            teacher_map.setdefault(instructor, []).append(f"{days} {time_str}")

    if not teacher_map:
        return ""

    lines = []
    for teacher, slots in teacher_map.items():
        seen = set()
        unique_slots = []
        for s in slots:
            if s not in seen:
                seen.add(s)
                unique_slots.append(s)
        lines.append(f"• {teacher}: {' | '.join(unique_slots)}")
    return "\n".join(lines)


def get_course_instructors(course_id: Optional[str] = None) -> List[str]:
    """Returns a deduplicated list of all canonical instructor names for a course,
    or across all active courses in the catalog if course_id is None.
    Dynamically parsed from the course JSONs.
    """
    from src.core.kb_loader import full_kb_course_ids
    cids = [course_id] if course_id else full_kb_course_ids()
    teachers: List[str] = []
    for cid in cids:
        for b in get_available_timings(cid):
            for inst in b.get("instructors", []):
                if inst and inst not in teachers:
                    teachers.append(inst)
    return teachers


def resolve_canonical_teacher(teacher_query: str, course_id: Optional[str] = None) -> Optional[str]:
    """Resolves colloquial, partial, or informal teacher references
    (e.g. 'madhuri mam', 'madhuri ji', 'madhuri ma\'am', 'sneha', 'kratika mam')
    to the exact canonical instructor name (e.g. 'Ms. Madhuri', 'Sneha Maurya', 'Kratika Gontiya').

    If course_id is provided, searches that course's roster first;
    if not matched (or course_id is None), searches all course rosters across the catalog.
    Returns canonical teacher name or None.
    """
    if not teacher_query or not str(teacher_query).strip():
        return None

    q_raw = str(teacher_query).strip()
    q_clean = re.sub(r"[^\w\s]", "", q_raw.lower())
    honorifics = {
        "ms", "miss", "mr", "mrs", "mam", "maam", "ji", "sir", "madam",
        "teacher", "guru", "dr", "faculty", "instructor"
    }
    tokens = [w for w in q_clean.split() if w not in honorifics]
    core_q = " ".join(tokens).strip()
    if not core_q or len(core_q) < 3:
        return None

    candidates = get_course_instructors(course_id)
    all_candidates = get_course_instructors(None) if course_id else candidates

    def clean_tokens(name: str) -> List[str]:
        c_clean = re.sub(r"[^\w\s]", "", name.lower())
        return [w for w in c_clean.split() if w not in honorifics]

    def find_in(pool: List[str]) -> Optional[str]:
        # 1. Exact match (case insensitive)
        for c in pool:
            if q_raw.lower() == c.lower():
                return c

        # 2. Exact match against cleaned core tokens
        for c in pool:
            c_tokens = clean_tokens(c)
            c_core = " ".join(c_tokens).strip()
            if core_q == c_core:
                return c

        # 3. Token match: if core_q is in candidate's tokens (e.g. 'madhuri' in 'ms. madhuri' or 'kratika' in 'kratika gontiya')
        for c in pool:
            c_tokens = clean_tokens(c)
            for ct in c_tokens:
                if len(ct) >= 3 and (core_q == ct or (len(core_q) >= 4 and (core_q in ct or ct in core_q))):
                    return c
        return None

    matched = find_in(candidates)
    if not matched and course_id:
        matched = find_in(all_candidates)
    return matched


# ── DAY CODES & CANONICAL NAMES ──
DAY_MAP_DISPLAY_TO_CODE = {
    # Tuesday / Thursday
    "tuesday/thursday": "TT",
    "tuesday & thursday": "TT",
    "tuesday and thursday": "TT",
    "tue/thu": "TT",
    "tue-thu": "TT",
    "tues thurs": "TT",
    "tue thu": "TT",
    "tue and thu": "TT",
    "tuesday thursday": "TT",
    "tues/thurs": "TT",
    "tt": "TT",
    "mangalvaar guruvaar": "TT",
    "mangalwar guruwar": "TT",
    "mangal aur guruwar": "TT",
    "mangalvaar": "TT",
    "mangalwar": "TT",
    "guruvaar": "TT",
    "guruwar": "TT",
    "tuesday": "TT",
    "thursday": "TT",
    "tue": "TT",
    "tues": "TT",
    "thu": "TT",
    "thur": "TT",
    "thurs": "TT",
    "t-t": "TT",

    # Wednesday / Friday
    "wednesday/friday": "WF",
    "wednesday & friday": "WF",
    "wednesday and friday": "WF",
    "wed/fri": "WF",
    "wed-fri": "WF",
    "wed fri": "WF",
    "wed and fri": "WF",
    "wednesday friday": "WF",
    "wf": "WF",
    "w-f": "WF",
    "budhvaar shukravaar": "WF",
    "budhwar shukrawar": "WF",
    "budh aur shukrawar": "WF",
    "budhvaar": "WF",
    "budhwar": "WF",
    "shukravaar": "WF",
    "shukrawar": "WF",
    "wednesday": "WF",
    "friday": "WF",
    "wed": "WF",
    "fri": "WF",

    # Saturday / Sunday
    "saturday/sunday": "SS",
    "saturday & sunday": "SS",
    "saturday and sunday": "SS",
    "sat/sun": "SS",
    "sat-sun": "SS",
    "sat sun": "SS",
    "sat and sun": "SS",
    "saturday sunday": "SS",
    "weekend": "SS",
    "weekends": "SS",
    "weekend batch": "SS",
    "ss": "SS",
    "s-s": "SS",
    "shanivaar ravivaar": "SS",
    "shaniwar raviwar": "SS",
    "shanivar ravivar": "SS",
    "shani aur raviwar": "SS",
    "shanivaar": "SS",
    "shaniwar": "SS",
    "shanivar": "SS",
    "ravivaar": "SS",
    "raviwar": "SS",
    "saturday": "SS",
    "sunday": "SS",
    "sat": "SS",
    "sun": "SS",

    # Monday to Friday
    "monday to friday": "MTWTF",
    "monday - friday": "MTWTF",
    "monday-friday": "MTWTF",
    "mon-fri": "MTWTF",
    "mon to fri": "MTWTF",
    "mon fri": "MTWTF",
    "monday through friday": "MTWTF",
    "daily": "MTWTF",
    "weekdays": "MTWTF",
    "weekday": "MTWTF",
    "weekday batch": "MTWTF",
    "mtwtf": "MTWTF",
    "m-f": "MTWTF",
    "mf": "MTWTF",
    "mon": "MTWTF",
    "monday": "MTWTF",
    "somvaar se shukravaar": "MTWTF",
    "somwar se shukrawar": "MTWTF",
    "som se shukra": "MTWTF",
    "somvaar": "MTWTF",
    "somwar": "MTWTF",

    # Tuesday / Thursday / Saturday
    "tuesday/thursday/saturday": "TTS",
    "tuesday & thursday & saturday": "TTS",
    "tts": "TTS",

    # Monday / Wednesday / Friday
    "monday/wednesday/friday": "MWF",
    "monday & wednesday & friday": "MWF",
    "mwf": "MWF",
}

CODE_TO_CANONICAL_DISPLAY = {
    "TT": "Tuesday/Thursday",
    "WF": "Wednesday/Friday",
    "SS": "Saturday/Sunday",
    "MTWTF": "Monday to Friday",
    "TTS": "Tuesday/Thursday/Saturday",
    "MWF": "Monday/Wednesday/Friday",
}


def get_day_code(day_input: Optional[str]) -> Optional[str]:
    """Returns the standardized 2-5 letter schedule code (e.g. 'TT', 'SS', 'WF', 'MTWTF') for any day string."""
    if not day_input or not str(day_input).strip():
        return None
    raw = str(day_input).strip().lower()
    if raw in DAY_MAP_DISPLAY_TO_CODE:
        return DAY_MAP_DISPLAY_TO_CODE[raw]

    # Clean punctuation and extra spaces
    cleaned = re.sub(r"[^\w/&-]", " ", raw)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if cleaned in DAY_MAP_DISPLAY_TO_CODE:
        return DAY_MAP_DISPLAY_TO_CODE[cleaned]

    # Substring token heuristics
    if ("sat" in raw or "shani" in raw) and ("sun" in raw or "ravi" in raw):
        return "SS"
    if ("tue" in raw or "mangal" in raw) and ("thu" in raw or "guru" in raw):
        return "TT"
    if ("wed" in raw or "budh" in raw) and ("fri" in raw or "shukra" in raw):
        return "WF"
    if "weekend" in raw:
        return "SS"
    if "weekday" in raw or "daily" in raw or ("mon" in raw and "fri" in raw):
        return "MTWTF"
    if raw in ("sat", "sun", "saturday", "sunday", "shani", "ravi"):
        return "SS"
    if raw in ("tue", "tues", "tuesday", "thu", "thur", "thurs", "thursday", "mangal", "guru"):
        return "TT"
    if raw in ("wed", "wednesday", "fri", "friday", "budh", "shukra"):
        return "WF"
    if raw in ("mon", "monday", "som", "somwar", "somvaar"):
        return "MTWTF"
    return None


def normalize_day_string(day_input: Optional[str]) -> Optional[str]:
    """Normalizes any user day input, quick reply, or abbreviation into canonical course display string
    (e.g. 'Tue & Thu' -> 'Tuesday/Thursday', 'weekend' -> 'Saturday/Sunday', 'daily' -> 'Monday to Friday').
    """
    code = get_day_code(day_input)
    if code and code in CODE_TO_CANONICAL_DISPLAY:
        return CODE_TO_CANONICAL_DISPLAY[code]
    return None


def format_days_for_button(day_str: str) -> str:
    """Formats day string for WhatsApp quick-reply button (<= 20 chars).
    e.g. 'Tuesday/Thursday' -> 'Tue & Thu'
         'Saturday/Sunday' -> 'Sat & Sun'
         'Wednesday/Friday' -> 'Wed & Fri'
         'Monday to Friday' -> 'Mon to Fri'
    """
    code = get_day_code(day_str)
    if code == "TT":
        return "Tue & Thu"
    elif code == "SS":
        return "Sat & Sun"
    elif code == "WF":
        return "Wed & Fri"
    elif code == "MTWTF":
        return "Mon to Fri"
    elif code == "TTS":
        return "Tue, Thu, Sat"
    elif code == "MWF":
        return "Mon, Wed, Fri"
    return str(day_str).replace("/", " & ")[:20]


def extract_time_tokens(time_str: Optional[str]):
    """Extracts (hour_12h, meridiem) from the start time of a timing string
    (e.g. '5:00 PM to 6:00 PM' -> (5, 'PM'), '17:00' -> (5, 'PM'), '06:03-07:00' -> (6, 'AM')).
    """
    if not time_str:
        return None, None
    m = re.search(r"(?:^|[^\d:])(\d{1,2})(?::(\d{2}))?\s*(am|pm)", str(time_str), re.IGNORECASE)
    if m:
        hr = int(m.group(1))
        merid = m.group(3).upper()
        return hr, merid
    m24 = re.search(r"(?:^|[^\d:])(\d{1,2}):(\d{2})", str(time_str))
    if m24:
        hr24 = int(m24.group(1))
        merid = "AM" if hr24 < 12 else "PM"
        hr12 = hr24 if 1 <= hr24 <= 12 else (hr24 - 12 if hr24 > 12 else 12)
        return hr12, merid
    return None, None


def get_available_days_for_batch(
    course_id: Optional[str] = None,
    timing: Optional[str] = None,
    teacher: Optional[str] = None,
) -> List[str]:
    """Returns a deduplicated list of available canonical day strings (e.g. ['Tuesday/Thursday', 'Saturday/Sunday'])
    for a given course, filtered by timing and/or instructor if provided.
    """
    if not course_id:
        return []

    batches = get_available_timings(course_id)
    if not batches:
        return []

    timing_query = str(timing).strip().lower() if timing else None
    t_clean = re.sub(r"[\s\-_–:]+", " ", timing_query) if timing_query else None
    q_hr, q_merid = extract_time_tokens(timing) if timing else (None, None)

    target_teacher = resolve_canonical_teacher(teacher, course_id=course_id) if teacher else None
    target_teacher_lower = target_teacher.lower() if target_teacher else (str(teacher).lower().strip() if teacher else None)

    matched_days: List[str] = []
    for b in batches:
        b_time = b.get("time_12h", "")
        b_days = b.get("days", "") or b.get("slot", "")
        b_insts = b.get("instructors", [])

        # 1. Timing filter (if timing provided)
        if timing:
            b_hr, b_merid = extract_time_tokens(b_time)
            b_time_clean = re.sub(r"[\s\-_–:]+", " ", b_time.lower())
            time_matches = False

            if q_hr is not None and q_merid is not None and b_hr is not None and b_merid is not None:
                # Exact start hour + meridiem match (e.g. 5 PM matches 5:00 PM to 6:00 PM, 17:00 matches 5:00 PM)
                if q_hr == b_hr and q_merid == b_merid:
                    time_matches = True
            elif timing_query and (timing_query in b_time.lower() or b_time.lower() in timing_query):
                time_matches = True
            elif t_clean and (t_clean in b_time_clean or b_time_clean in t_clean):
                time_matches = True

            if not time_matches:
                continue

        # 2. Teacher filter (if teacher provided)
        if target_teacher_lower:
            teacher_matches = False
            for inst in b_insts:
                inst_canonical = resolve_canonical_teacher(inst, course_id=course_id) or inst
                if (
                    target_teacher_lower == inst.lower()
                    or target_teacher_lower == inst_canonical.lower()
                    or target_teacher_lower in inst.lower()
                    or inst.lower() in target_teacher_lower
                ):
                    teacher_matches = True
                    break
            if not teacher_matches:
                continue

        canonical_day = normalize_day_string(b_days) or b_days.strip()
        if canonical_day and canonical_day not in matched_days:
            matched_days.append(canonical_day)

    # Robust fallback: If strict intersection of timing + teacher yielded no matches (due to slot clash),
    # fall back to teacher's days, timing's days, or course-wide days!
    if not matched_days:
        if teacher and timing:
            fallback_teacher = get_available_days_for_batch(course_id=course_id, teacher=teacher)
            if fallback_teacher:
                return fallback_teacher
            fallback_timing = get_available_days_for_batch(course_id=course_id, timing=timing)
            if fallback_timing:
                return fallback_timing
        all_course_days = []
        for b in batches:
            d = normalize_day_string(b.get("days", "") or b.get("slot", "")) or (b.get("days", "") or "").strip()
            if d and d not in all_course_days:
                all_course_days.append(d)
        if len(all_course_days) == 1:
            return all_course_days

    return matched_days
