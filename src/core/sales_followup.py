"""
sales_followup.py — Active Sales Counsellor Follow-up Engine.

Generates context-aware, stage-specific open-ended follow-up questions
to append after every bot reply — turning passive FAQ responses into
active sales conversations.

HOW TO EDIT (no other file needs to change):
  - Add / change questions   -> edit _QUESTION_BANK
  - Add / change fallbacks   -> edit _FALLBACK_BY_STAGE
  - Suppress new topics      -> edit _SUPPRESS_ON_USER_KWS / _SUPPRESS_ON_REPLY_KWS
"""

from typing import Optional


# -- Suppression Rules --------------------------------------------------------
# If ANY of these match the user message, no follow-up question is appended.
_SUPPRESS_ON_USER_KWS = [
    # Medical / Sensitive
    "cancer", "heart", "cardiac", "surgery", "doctor", "medical", "treatment",
    "disease", "illness", "bp", "blood pressure", "diabetes", "sugar",
    "spine", "spinal", "slip disc", "slipped disc", "injury", "fracture",
    "pregnant", "pregnancy", "prenatal", "postnatal", "garbhasanskar",
    "operation", "paralysis", "stroke", "kidney", "liver", "asthma",
    "arthritis", "tumour", "tumor", "chemo", "chemotherapy", "patient",
    "dawa", "hospital", "bimari", "bimar",
    # Unoffered services
    "kids yoga", "face yoga", "1-on-1", "one on one", "private class",
    "home tutor", "personal class",
    # Refund / Support / Complaint / Legal / Disputes
    "refund", "cancel", "cancellation", "complaint", "dispute", "police",
    "legal", "agent", "support", "policy", "attendance", "reschedule", "pause",
]

# If ANY of these appear in the bot reply, no follow-up is appended.
_SUPPRESS_ON_REPLY_KWS = [
    # Explicit unoffered services in replies
    "prenatal yoga", "kids yoga", "face yoga", "1-on-1", "private class",
    "personal class", "home tutor", "offline classes",
    # Agent handoff / escalation suggestions
    "type *agent*", "support team can assist", "non-refundable", "non refundable",
    "agent type karein", "type agent", "kripya agent", "support team",
    "absence allowed nahi", "leave ya absence allowed nahi",
]


# Stages where no sales follow-up is ever added
_SKIP_STAGES = {"PROFILE_COMPLETED", "COUPON_SENT", "READY_FOR_APP_LINK", "APP_LINK_SENT", "TRIAL_REQUESTED", "TRIAL_STEPS_SENT"}



# -- Contextual Question Bank -------------------------------------------------
# Priority: first matching entry wins. Put most specific entries first.
# Each entry: triggers (list), hindi (str), english (str)

_QUESTION_BANK = [
    {
        "triggers": [
            "fee", "fees", "price", "cost", "kitna", "charges", "rupee", "paise", "paisa", "rupaye",
            "mahanga", "expensive", "costly", "zyada",
        ],
        "hindi": "Kaunsa time slot aapke liye best rahega, subah ya shaam? 😊",
        "english": "Which time slot suits you better, morning or evening? 😊",
    },
    {
        "triggers": [
            "teacher", "trainer", "instructor", "coach", "mam", "ma'am",
            "sir", "sikhane", "sikhata", "sikhati",
        ],
        "hindi": (
            "Aap kis teacher ya timing ka trial lena chahenge? 😊"
        ),
        "english": (
            "Which teacher or batch timing would you like to try? 😊"
        ),
    },
    {
        "triggers": [
            "syllabus", "course", "kya sikhate", "curriculum", "topics",
            "asana", "hatha", "pranayama", "meditation", "what do you teach",
            "kitne type", "kaun kaun",
        ],
        "hindi": (
            "Aapka main focus kya hai — weight loss, stress relief, ya flexibility? "
            "Main ussi ke hisaab se best batch suggest kar sakta hoon. 😊"
        ),
        "english": (
            "What is your main goal — weight loss, stress relief, or flexibility? "
            "I can suggest the best batch for you based on that. 😊"
        ),
    },
    {
        "triggers": [
            "batch size", "kitne students", "how many students",
            "individual", "personal attention", "focus kaise", "dhyan",
        ],
        "hindi": (
            "Live interactive format mein aap directly teacher se class ke dauran pooch sakte hain. "
            "Kya aap ek free trial try karke dekh sakte hain? 😊"
        ),
        "english": (
            "In our live interactive format, you can ask the teacher directly during class. "
            "Would you like to try a free trial to see for yourself? 😊"
        ),
    },
    {
        "triggers": [
            "timing", "schedule", "kab", "kaunsa time",
            "morning", "evening", "subah", "shaam", "raat", "dopahar",
            "which batch", "kon sa batch", "kon si timing",
        ],
        "hindi": "Subah ya shaam — kaun sa time aapke daily routine mein fit hoga? 😊",
        "english": "Morning, Afternoon or evening — which time fits your daily routine better? 😊",
    },
    {
        "triggers": [
            "review", "rating", "trust", "legit", "fraud", "fake",
            "real", "genuine", "testimonial", "sach", "verified",
        ],
        "hindi": (
            "Aap ek free trial leke khud experience kar sakte hain — koi risk nahi. "
            "Kya aap try karna chahenge? 😊"
        ),
        "english": (
            "You can experience it yourself with a free trial — absolutely no risk. "
            "Would you like to give it a try? 😊"
        ),
    },
    {
        "triggers": ["trial", "demo", "free class", "sample", "pehle try"],
        "hindi": (
            "Trial book karne ke baad koi sawaal ho — timing ya teacher ke baare mein — "
            "toh zaroor poochein. Kya aap trial ke liye ready hain? 😊"
        ),
        "english": (
            "Feel free to ask anything after booking the trial — about timing or teachers. "
            "Are you ready to book your trial? 😊"
        ),
    },
    {
        "triggers": [
            "location", "address", "kahan", "where", "branch",
            "online hai", "offline", "center", "studio", "ghar se",
        ],
        "hindi": (
            "Classes 100% online hain Sensationz App par — ghar se hi attend kar sakte hain. "
            "Kya aap morning ya evening prefer karenge? 😊"
        ),
        "english": (
            "Classes are 100% online via the Sensationz App — attend from anywhere. "
            "Do you prefer morning or evening? 😊"
        ),
    },
    {
        "triggers": [
            "new batch", "joining", "kab se", "when start", "start kab",
            "join kab", "abhi join", "aaj se",
        ],
        "hindi": (
            "Batches ongoing hain — aap abhi join karke aaj ki class se start kar sakte hain. "
            "Kaunsa timing suit karega aapko? 😊"
        ),
        "english": (
            "Batches are ongoing — you can join today and start right away. "
            "Which timing works best for you? 😊"
        ),
    },
    {
        "triggers": [
            "app", "download", "play store", "app store", "mobile",
            "kaise chalata", "kaise join",
        ],
        "hindi": (
            "App download karna kaafi simple hai — 5 minutes mein set ho jaata hai. "
            "Kya aapko download steps mein help chahiye? 😊"
        ),
        "english": (
            "Downloading the app is a simple 5-minute process. "
            "Would you like help with the download steps? 😊"
        ),
    },
]


# -- Stage-Aware Fallback Questions -------------------------------------------
# Used when no keyword entry matches.

_FALLBACK_BY_STAGE = {
    "NEW": {
        "hindi": "Yoga classes ke baare mein kuch aur jaanna chahte hain, ya timing discuss karein? 😊",
        "english": "Would you like to know more about the classes, or shall we discuss timings? 😊",
    },
    "ENROLL_ASKED": {
        "hindi": "Kya aap available timings dekhna chahenge? 😊",
        "english": "Would you like to see the available timings? 😊",
    },
    "ENROLL_CONFIRMED": {
        "hindi": "Subah ya shaam — kaun sa time aapke liye best rahega? 😊",
        "english": "Morning or evening — which time suits you better? 😊",
    },
    "TIMING_SELECTED": {
        "hindi": "Kitne time ke liye join karna chahenge — 1 month trial se shuru karein ya seedha 3 months? 😊",
        "english": "How long would you like to join — start with 1 month or go for 3 months? 😊",
    },
    "PACKAGE_ASKED": {
        "hindi": (
            "Kaunsa package aapke liye best rahega? 😊\n\n"
            "Fees:\n"
            "• 1 Month: ₹700 (Offer Price: ₹300)\n"
            "• 3 Months: ₹1,750 (Offer Price: ₹600)\n"
            "• 6 Months: ₹3,200 (Offer Price: ₹1,000)\n"
            "• 1 Year: ₹5,000 (Offer Price: ₹1,800)\n\n"
            "Offer price app aur welcome coupon ke through applicable hoga ✨"
        ),
        "english": (
            "Which package works best for you? 😊\n\n"
            "Fees:\n"
            "• 1 Month: ₹700 (Offer Price: ₹300)\n"
            "• 3 Months: ₹1,750 (Offer Price: ₹600)\n"
            "• 6 Months: ₹3,200 (Offer Price: ₹1,000)\n"
            "• 1 Year: ₹5,000 (Offer Price: ₹1,800)\n\n"
            "Offer price is applicable through the app and welcome coupon ✨"
        ),
    },
    "PACKAGE_SELECTED": {
        "hindi": "Bahut badiya! App download karne mein koi help chahiye? 😊",
        "english": "Great choice! Need help downloading the app? 😊",
    },
}


# -- Helpers ------------------------------------------------------------------

def _is_hindi(text: str) -> bool:
    """Detects Hindi/Hinglish by Devanagari script or common Hindi words."""
    HINDI_MARKERS = [
        "kya", "hai", "mujhe", "batao", "chahiye", "ka", "ki", "ke",
        "nahi", "haan", "aur", "se", "bhi", "kab", "kaise", "kitna",
        "subah", "shaam", "abhi", "mat", "nhi", "hota", "hoti",
        "mein", "pe", "par", "toh",
    ]
    has_devanagari = any("\u0900" <= ch <= "\u097F" for ch in text)
    has_hindi_word = any(w in text.lower().split() for w in HINDI_MARKERS)
    return has_devanagari or has_hindi_word


def _should_suppress(user_text: str, full_reply: str) -> bool:
    """Returns True if no follow-up question should be added."""
    try:
        from tasks import is_disinterest_signal
        if is_disinterest_signal(user_text):
            return True
    except Exception:
        pass
    u = user_text.lower()
    r = full_reply.lower()
    if any(kw in u for kw in _SUPPRESS_ON_USER_KWS):
        return True
    if any(kw in r for kw in _SUPPRESS_ON_REPLY_KWS):
        return True
    return False


# -- Public API ---------------------------------------------------------------

def get_sales_followup(user_text: str, full_reply: str, state: dict) -> Optional[str]:
    """
    Returns a context-aware, stage-sensitive follow-up question to send
    AFTER the bot reply as a separate WhatsApp message, or None if suppressed.
    """
    if _should_suppress(user_text, full_reply):
        return None

    u_lower = user_text.lower()
    use_hindi = _is_hindi(user_text)

    # 1. Location / where inquiry (Context-aware: don't ask morning/evening if timing is already chosen)
    location_kws = ["location", "address", "kahan", "where", "branch", "online hai", "offline", "center", "studio", "ghar se"]
    if any(kw in u_lower for kw in location_kws):
        if state.get("timing"):
            return (
                "Online live class attend karne ke baare mein koi aur sawaal hai? 😊"
                if use_hindi else
                "Do you have any questions about attending the online live classes? 😊"
            )
        else:
            return (
                "Classes 100% online hain Sensationz App par — ghar se hi attend kar sakte hain. Kya aap morning ya evening prefer karenge? 😊"
                if use_hindi else
                "Classes are 100% online via the Sensationz App — attend from anywhere. Do you prefer morning or evening? 😊"
            )

    # 2. Fee / Pricing / Discount Inquiry (Context-aware: if timing is already selected, ask for package duration)
    fee_kws = [
        "fee", "fees", "price", "cost", "kitna", "charges", "rupee", "paise", "paisa", "rupaye", "rupay",
        "payment", "pay", "dene", "pdnge", "padenge", "lagenge", "lagega",
        "mahanga", "expensive", "costly", "zyada", "discount", "offer", "coupons", "coupon"
    ]
    if any(kw in u_lower for kw in fee_kws):
        if state.get("timing") and not state.get("package"):
            return (
                "Aap kitne duration ke liye start karna chahenge — 1 month ya 3 months? 😊"
                if use_hindi else
                "Which package duration would you like to start with — 1 month or 3 months? 😊"
            )
        elif state.get("timing") and state.get("package"):
            return (
                "App download karke profile banane mein koi help chahiye? 😊"
                if use_hindi else
                "Need any help downloading the app and creating your profile? 😊"
            )
        else:
            return (
                "Kaunsa time slot aapke liye best rahega, subah ya shaam? 😊"
                if use_hindi else
                "Which time slot suits you better, morning or evening? 😊"
            )

    # 3. Timing / Schedule Inquiry (Context-aware: if timing is already selected, don't ask again)
    timing_kws = [
        "timing", "schedule", "kab", "kaunsa time",
        "morning", "evening", "subah", "shaam", "raat", "dopahar", "afternoon", "aftern", "aftrnoon",
        "aftrn", "aftn", "dophar", "dupehar", "dupahar", "mrng", "morng", "evng",
        "which batch", "kon sa batch", "kon si timing"
    ]
    if any(kw in u_lower for kw in timing_kws):
        if state.get("timing") and not state.get("package"):
            return (
                "Aap kitne duration ke liye start karna chahenge — 1 month ya 3 months? 😊"
                if use_hindi else
                "Which package duration would you like to start with — 1 month or 3 months? 😊"
            )
        elif state.get("timing") and state.get("package"):
            return (
                "App download karke profile banane mein koi help chahiye? 😊"
                if use_hindi else
                "Need any help downloading the app and creating your profile? 😊"
            )
        else:
            return (
                "Subah ya shaam — kaun sa time aapke daily routine mein fit hoga? 😊"
                if use_hindi else
                "Morning, Afternoon or evening — which time fits your daily routine better? 😊"
            )

    # 3b. Conversational Acknowledgments / Confirmations ("ok", "okay", "theek hai", "got it", "fine", etc.)
    ack_words = ["ok", "okay", "theek", "theek hai", "thik", "thik hai", "accha", "acha", "got it", "understood", "fine", "k", "sahi", "sahi hai"]
    tokens = [w.strip(".,!?:;\"'") for w in u_lower.split()]
    is_ack = u_lower in ack_words or (len(tokens) <= 2 and any(w in ack_words for w in tokens))
    if is_ack:
        if state.get("timing") and not state.get("package"):
            return (
                "Aap kitne duration ke liye start karna chahenge — 1 month ya 3 months? 😊"
                if use_hindi else
                "Which package duration would you like to start with — 1 month or 3 months? 😊"
            )
        elif not state.get("timing"):
            return (
                "Subah ya shaam — kaun sa time aapke daily routine mein fit hoga? 😊"
                if use_hindi else
                "Morning, afternoon or evening — which time fits your daily routine better? 😊"
            )
        elif state.get("timing") and state.get("package") and not state.get("profile_created"):
            return (
                "App download karke profile banane mein koi help chahiye? 😊"
                if use_hindi else
                "Need any help downloading the app and creating your profile? 😊"
            )

    # 4. Keyword-matched question (first match wins)
    for entry in _QUESTION_BANK:
        if any(kw in u_lower for kw in entry["triggers"]):
            return entry["hindi"] if use_hindi else entry["english"]

    # 5. Stage-aware fallback (skip if stage is in _SKIP_STAGES or wants_trial)
    if state.get("stage") in _SKIP_STAGES or state.get("wants_trial"):
        return None

    stage = state.get("stage", "NEW")
    if stage in _FALLBACK_BY_STAGE:
        q = _FALLBACK_BY_STAGE[stage]
        return q["hindi"] if use_hindi else q["english"]

    return None


