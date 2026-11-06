"""
intent_classifier.py — Two-Step Semantic Extractor (Silent Structured JSON).

Reads incoming customer messages in any language, dialect, or script (Hindi, Hinglish, English, Tamil, etc.)
and extracts pure semantic state changes and customer intent WITHOUT fragile hardcoded regexes.
"""

import os
import re
import json
from typing import Optional, Dict, Any
from dotenv import load_dotenv
from langchain_core.messages import SystemMessage, HumanMessage

_local_env = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".env")
load_dotenv(_local_env)
load_dotenv()

_classifier_llm = None


def _get_classifier_llm():
    """Lazy-initialize a lightweight LLM for fast, accurate structured extraction,
    routed through OpenRouter when configured, with OpenAI fallback."""
    global _classifier_llm
    if _classifier_llm is None:
        from langchain_openai import ChatOpenAI
        openrouter_key = os.getenv("OPENROUTER_API_KEY")
        if openrouter_key:
            model_name = os.getenv("OPENROUTER_EXTRACTOR_MODEL", "openai/gpt-4o-mini")
            _classifier_llm = ChatOpenAI(
                model=model_name,
                api_key=openrouter_key,
                base_url="https://openrouter.ai/api/v1",
                temperature=0,
                timeout=22,
                max_retries=0,
            )
        else:
            openai_key = os.getenv("OPENAI_API_KEY")
            model_name = os.getenv("OPENAI_EXTRACTOR_MODEL") or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
            _classifier_llm = ChatOpenAI(
                model=model_name,
                api_key=openai_key,
                temperature=0,
                timeout=22,
                max_retries=0,
            )
    return _classifier_llm


def _empty_flags() -> Dict[str, Any]:
    """
    Safe fallback for extractor failure.
    Guarantees silent failure, not silent corruption:
    If the extractor errors or returns garbage, it becomes a safe no-op turn
    rather than wrongly flipping someone's confirmed/unconfirmed state.
    """
    return {
        "app_installed_stated": "unmentioned",
        "profile_created_stated": "unmentioned",
        "selected_timing": None,
        "timing_period": None,
        "selected_teacher": None,
        "selected_days": None,
        "selected_package": None,
        "selected_course": None,
        "wants_trial": False,
        "wants_demo_video": False,
        "wants_app_link": False,
        "requests_qr": False,
        "requests_human_agent": False,
        "wants_phone_call": False,
        "is_coupon_requested": False,
        "is_disinterested": False,
        "is_complaint_or_refund": False,
        "is_out_of_scope": False,
        "customer_intent": "QUESTION",
        "question_summary": None,
        "detected_language": "English",
        "explicit_language_request": None,
        "wants_resend_video": False,
        "claims_payment_or_enrollment": False,
        "cannot_access_classes": False,
        "timing_ambiguous": False,
        "ambiguous_hour": None,
        "ambiguous_timing_candidates": None,
        "timing_resolution_status": "NONE",
        "timing_selection_attempted": False,
        "requested_start_24h": None,
        "requested_end_24h": None,
        "requested_timing_period": None,
        "wants_demo": False,
        "wants_app_guide": False,
        "wants_app_video_guide": False,
        "wants_trial_guide": False,
        "wants_trial_video_guide": False,
        "unoffered_type": None,
        "unoffered_value": None,
        "unoffered_timing_mentioned": None,
        "conversation_summary": None,
    }


EXTRACTOR_SYSTEM_PROMPT = """You are a silent, highly accurate Semantic State Extractor for a WhatsApp course sales assistant.
Your job is ONLY to analyze the customer's message and determine what state changes, confirmations, or intents occurred.
You NEVER write customer-facing replies. You output ONLY valid, strict JSON.

LANGUAGE & SCRIPT AGNOSTIC:
Customers write in English, Hindi (Devanagari or Hinglish/Latin script), or other Indian languages with informal phrasing, typos, or slang.
Judge by SEMANTIC MEANING, NEVER by exact keyword presence.

EXTRACTION FIELDS:

1. "app_installed_stated":
   - "confirmed": Customer states/confirms they downloaded or installed the Sensationz App ("download kar liya", "install ho gaya", "app to ho gaya", "downloaded", "installed", "done app").
   - "denied": Customer states they have NOT downloaded it ("nahi kiya", "haven't downloaded", "app download nahi kiya").
   - "unmentioned": Not stated.

2. "profile_created_stated":
   - "confirmed": Customer states/confirms they created their profile, account, or finished signup ("profile bana li", "done", "yes done", "banadi", "created", "account ban gaya", "sab kar diya", "haan ho gaya", "krlia"). Note: If the customer replies "done", "yes", or "krlia" after the bot asked them to create their profile or install the app, this is "confirmed".
   - "denied": Customer states they have NOT created their profile or skipped it ("profile abhi nahi", "profile skip kar diya", "profile nahi banayi", "haven't done profile yet").
   - "unmentioned": Not stated.
   - CRITICAL NEGATIVE GUARD: If customer says "paid", "payment done", "paise bhej diye", or "fees bhar di", this is a PAYMENT CLAIM, NOT profile creation! Set profile_created_stated to "unmentioned" unless they explicitly mentioned app profile/signup.

CRITICAL PARTIAL / NEGATION RULE:
If customer says "app to ho gaya but profile abhi nahi" -> app_installed_stated: "confirmed", profile_created_stated: "denied".
If customer says "nahi abhi tak nahi kiya" -> app_installed_stated: "denied", profile_created_stated: "denied".

3. "selected_timing":
   - Batch timing explicitly and definitively chosen by the user from the Allowed Canonical Batch Timings in context.
   - You MUST translate and map colloquial or informal timing references (e.g. "morning 7 am", "shaam 6 baje", "7-8", "6 to 7 evening", "12-1", "10-11") directly to the exact matching canonical batch timing string from the Allowed Canonical Batch Timings in context (e.g. "7:00 AM to 8:00 AM", "6:00 PM to 7:00 PM", "12:00 PM to 1:00 PM", "10:00 AM to 11:00 AM").
   - COLLOQUIAL & BARE TIMINGS INTELLIGENCE:
     * Numbers with colloquial Hindi/Hinglish hour markers like "7bje", "7 baje", "6 baje", "8 baje", or bare numbers like "7", "6", "8" refer to that specific clock hour.
     * Check the Allowed Canonical Batch Timings for the active course:
       • If that hour exists in BOTH Morning and Evening (for example, Yoga has BOTH "7:00 AM to 8:00 AM" and "7:00 PM to 8:00 PM"):
         - The hour IS OFFERED, but AMBIGUOUS.
         - The brain is STRICTLY FORBIDDEN from marking it as unoffered_type: "TIMING"!
         - Set "timing_ambiguous": true.
         - Set "selected_timing": null.
         - Set "customer_intent": "TIMING_SELECTION".
         - Set "unoffered_type": null, "unoffered_value": null, and "unoffered_timing_mentioned": null.
         - Set "question_summary": "Customer selected 7 o'clock; ask them whether they prefer Morning 7:00 AM to 8:00 AM or Evening 7:00 PM to 8:00 PM."
       • If that hour exists in ONLY ONE slot in the course schedule:
         - Automatically resolve and map directly to that exact canonical slot string.
         - Set "timing_ambiguous": false.
         - Set "unoffered_type": null.
       • If that hour does NOT exist at all in the Allowed Canonical Batch Timings in either AM or PM :
         - Set "selected_timing": null, set "timing_ambiguous": false, set "unoffered_type": "TIMING", set "unoffered_value" to the requested timing, and set "customer_intent": "QUESTION".
   - PERIOD CLARIFICATION & BROAD PERIOD RULE (MULTILINGUAL):
     * CASE A: If 'Pending Ambiguous Timing Hour' is set in context (or the assistant just asked to choose between Morning and Evening for a specific hour):
     * When the customer responds with their period preference in ANY language, script, dialect, typo, or slang (e.g. English "morning", "evening", "am", "pm", Hindi/Hinglish "subah", "sbh", "sbh ka", "subha", "sawere", "savere", "mrng", "morng", "shaam", "sham", "sham ka", "raat", Bengali "shokal", Tamil "kaalai", Telugu "udayam", Marathi "sakali", Gujarati "savare", etc.):
       • Immediately resolve and map it to the corresponding canonical slot for that pending hour from Allowed Canonical Batch Timings (for example, if pending hour is 7: "sbh" -> "7:00 AM to 8:00 AM", "sham" -> "7:00 PM to 8:00 PM").

       • Set "selected_timing" to that exact canonical string.
       • Set "timing_ambiguous": false, "ambiguous_hour": null, "customer_intent": "TIMING_SELECTION".
       • Set "unoffered_type": null, "unoffered_value": null.
     * CASE B: If NO pending ambiguous hour is set in context, and the customer asks for or mentions a broad period WITHOUT any clock number (e.g. "subah ka batch", "sbh", "subah", "morning", "shaam", "evening", "afternoon", "dopahar"):
       • Broad periods contain multiple distinct batches. You are STRICTLY FORBIDDEN from guessing, picking, assuming, or auto-assigning an exact hourly slot (such as 5:00 AM)!
       • Set "selected_timing": null.
       • Set "requested_timing_period" to that period (e.g. "Morning", "Evening", "Afternoon").
       • Set "requested_start_24h": null and "requested_end_24h": null (NEVER invent hours).
       • Set "customer_intent": "TIMING_SELECTION".
       • Set "timing_ambiguous": false.


   - Output ONLY the exact matching canonical string from the Allowed Canonical Batch Timings list, or null.
   - Also return the customer's requested interval independent of the catalog:
     "requested_start_24h" and "requested_end_24h" as zero-padded HH:MM strings,
     or null when no numeric time/range was stated. Return the requested period
     in "requested_timing_period" using the period name from the catalog slot
     (for example Morning, Afternoon, or Evening), or null. Never substitute a
     nearby catalog batch into these fields.
   - CRITICAL UNAVAILABLE / UNOFFERED TIMING GUARD:
     • If the customer asks for, selects, or mentions a timing that is NOT in the Allowed Canonical Batch Timings (for example, "3", "3pm", "3:00", "11-12", "11 to 12", "2 to 3", "9 pm", or any unlisted hour), set selected_timing to null!
     • NEVER guess, invent, snap, or substitute an unoffered timing to an adjacent or nearby slot (e.g. NEVER convert "11-12" to "12:00 PM to 1:00 PM", and NEVER convert "3" to "6:00 AM to 7:00 AM")!
     • When the customer gives an unoffered timing, set selected_timing to null, set unoffered_type to "TIMING", set unoffered_value to the requested timing (e.g. "3:00" or "11-12"), set unoffered_timing_mentioned to the requested timing, and set customer_intent to "QUESTION".
   - CRITICAL NEGATIVE GUARD: Set to null if the customer is asking a general question, expressing doubt, or asking about schedule flexibility or switching batches (e.g. "1 week morning and 1 week evening", "can I switch timing?", "subah aur shaam dono chalega?", "timing change ho sakti hai kya?", "is this ok?"). In these cases, customer_intent is "QUESTION" and selected_timing MUST BE null!

4. "selected_package":
   - Package duration definitively chosen by the user in ANY language, script, or dialect worldwide (e.g. English "1 month", "m3", "3m", Hindi "1 महीना", "3 mahine", Tamil "3 மாதங்கள்", Malayalam "3 മാസം", Telugu "మూడు నెలలు", Spanish "tres meses", etc.).
   - CRITICAL MULTILINGUAL RULE: Package durations in ANY language or script that correspond to 1 Month, 3 Months, 6 Months, or 1 Year are 100% OFFERED canonical packages! You MUST translate and map it directly to the exact canonical string from Allowed Canonical Packages (e.g. "1 Month", "3 Months", "6 Months", "1 Year"), set "customer_intent" to "PACKAGE_SELECTION", and set "unoffered_type" to null! NEVER mark an allowed package in another language as "unoffered_type: PACKAGE"!
   - You MUST translate and map it directly to the exact matching canonical package string from the Allowed Canonical Packages in context (e.g. "1 Month", "3 Months", "6 Months", "1 Year").
   - Output ONLY the exact canonical English string from the allowed list, or null. NEVER output foreign language text or unlisted intervals.
   - CRITICAL UNAVAILABLE / UNOFFERED PACKAGE GUARD:
     • If customer asks for, selects, or mentions a duration or package NOT in Allowed Canonical Packages (e.g. "2 years", "2 yearly", "5 years", "2 months", "15 days", "6 weeks", "lifetime"), set selected_package to null!
     • NEVER guess, invent, round, snap, or substitute an unoffered package to an allowed package (e.g. NEVER convert "2 yearly" or "2 years" to "1 Year")!
     • Set unoffered_type to "PACKAGE", set unoffered_value to the requested duration (e.g. "2 Years"), and set customer_intent to "QUESTION".
   - CRITICAL NEGATIVE GUARD: NEVER extract "1 week", "2 days", or arbitrary intervals as a package! Sensationz only offers packages listed in the course catalog. Set to null if the customer is asking a question or expressing doubt.

5. "selected_teacher":
   - Specific instructor or teacher definitively chosen, preferred, or requested by the user (e.g. "madhuri mam", "madhuri ji", "madhuri", "sneha", "mradula", "sonali mam", "parul mam", "aditi").
   - You MUST translate and map colloquial or informal teacher references directly to the exact matching canonical teacher name from the Allowed Canonical Instructors in context (e.g. "madhuri mam" -> "Ms. Madhuri", "kratika" -> "Kratika Gontiya", "sneha" -> "Sneha Maurya", "vibha" -> "Vibha Mittal").
   - Output ONLY the exact canonical string from the allowed list, or null. NEVER invent unlisted names.
   - CRITICAL UNOFFERED INSTRUCTOR GUARD:
     • If customer asks for, selects, or mentions an instructor who does not conduct the active course (e.g. "Deepanshu", "Rahul"), set selected_teacher to null!
     • Set unoffered_type to "TEACHER", set unoffered_value to the requested instructor name, and set customer_intent to "QUESTION".
   - CRITICAL NEGATIVE GUARD: Set to null if the customer is asking a general informational question about teachers (e.g. "who teaches?", "teachers kaun hain?", "faculty details", "who is the teacher for 5pm?"). In these cases, customer_intent is "QUESTION" and selected_teacher MUST BE null!

6. "selected_days":
   - Specific batch/class days definitively chosen, preferred, or requested by the user from the Allowed Canonical Batch Days in context (e.g. "Tuesday/Thursday", "Saturday/Sunday", "Wednesday/Friday", "Monday to Friday", "weekend", "weekdays", "Tue/Thu", "Sat/Sun").
   - DAY SHORTHAND & CHAT ABBREVIATIONS INTELLIGENCE:
     * "ss", "s-s", "s/s", "sat sun", "sat-sun", "weekend" -> "Saturday/Sunday"
     * "tt", "t-t", "t/t", "tue thu", "tue-thu" -> "Tuesday/Thursday"
     * "wf", "w-f", "w/f", "wed fri", "wed-fri" -> "Wednesday/Friday"
     * "mf", "m-f", "mon fri", "mon-fri", "weekdays" -> "Monday to Friday"
   - Output ONLY the canonical string from Allowed Canonical Batch Days (e.g. "Tuesday/Thursday", "Saturday/Sunday", "Wednesday/Friday", "Monday to Friday"), or null.

7. "selected_course":
   - Course mentioned by user ("yoga", "face yoga", "kathak", "western dance", "zumba", "music", "guitar", "singing", "drawing", "acting", etc.), else null.
   - CRITICAL UNOFFERED COURSE GUARD:
     • If customer asks for an unoffered course type or style not offered by Sensationz (e.g. "prenatal yoga", "swimming", "karate"), set selected_course to null!
     • Set unoffered_type to "COURSE", set unoffered_value to the requested course name, and set customer_intent to "QUESTION".

8. "wants_trial":
   - boolean: true if customer asks for demo class, trial class, free trial, or wanting to try a live class before enrolling.

8a. "wants_demo_video":
   - boolean: true if customer asks to see an instructor demo, dance/yoga sample video, teacher performance preview, or wants to see how a teacher teaches before enrolling (e.g. "demo video", "madhuri mam video dikhao", "dance video", "sample video", "teacher preview", "demo dikhao", "teaching video"). Otherwise false.

8b. "wants_app_link":
   - boolean: true if customer asks to receive or resend the app download link or website link, OR chooses/taps an app store platform button (e.g. "android app", "ios app", "app link", "download link", "play store", "app store", "send link", "link bhejo", "link do", "konsi app", "which app"). Otherwise false.

8c. "requests_qr":
   - boolean: true if customer wants to pay online, asks how to pay, asks for UPI QR code, payment scanner, or payment link (e.g. "pay online", "payment qr", "send qr", "scanner bhejo", "payment link", "how to pay", "kahan pay karein", "upi id bhejo"). Otherwise false.

9. "requests_human_agent":
   - boolean: true ONLY if customer explicitly requests a human agent or coordinator to take over the chat ("agent", "human", "talk to human in chat", "representative").
   - NEGATIVE GUARD: If customer asks to speak on the phone, connect on a call, or asks for phone numbers to call, do NOT set this to true — set "wants_phone_call" to true instead!
   - NEGATIVE GUARD: DO NOT set to true for general help words or button responses ("help me", "need help", "yes help me", "madad chahiye", "guide me", "kya karna hai", "help with coupon", "help with app"). Those are informational queries, NOT human escalation!

9b. "wants_phone_call":
   - boolean: true ONLY if customer explicitly wants to connect on a phone call, speak over the phone, asks for phone numbers to call, or asks someone to call them on phone (e.g. "call pe baat karni hai", "can we talk on call?", "give phone number", "call me", "phone number do", "call pe samjha do", "phone support", "contact number", "call karo", "phone karo", "talk on phone"). Otherwise false.

10. "is_coupon_requested":
   - boolean: true if customer explicitly asks to receive, see, or apply their coupon code ("coupon code bhejo", "code kya hai", "discount code", "coupon do", "where is my coupon").

11. "is_disinterested":
    - boolean: true if the customer refuses, declines, rejects, or signals they will NOT enroll.
    - Includes BOTH explicit and CONTEXTUAL/IMPLICIT refusals:
      • Explicit: "not interested", "nahi chahiye", "cancel", "don't message", "nahi lena", "rehne do" (standalone rejections only).
       • CONTRASTIVE SELECTION GUARD (MANDATORY): If the customer says "no" as part of a correction, preference update, or slot selection (e.g. "no i want 5pm", "no 6pm", "no, evening please", "no 1 month", "no change to 5pm", "nahi shaam ko", "nahi dusra timing chahiye"), this is a TIMING/PACKAGE/TEACHER SELECTION, NOT disinterest! "is_disinterested" MUST be false, and "customer_intent" must be the corresponding selection intent (e.g. "TIMING_SELECTION")!
      • Contextual: Customer explicitly states that an unmet requirement is a dealbreaker preventing enrollment, ONLY AFTER having been informed of our policy (e.g. "then I won't join", "offline nahi hai to rehne do", "fixed schedule nahi ho payega mujhse, cancel karo").
      • Context-aware: If the PREVIOUS AI MESSAGE informed the customer of a policy limitation (e.g. no flexible timings, online only, fixed schedule) AND the customer now expresses dissatisfaction with that limitation or says "no" / declines — this IS disinterest.
      • QUESTION-ANSWER ADJACENCY RULE (CRITICAL): When the AI in the recent transcript asks why the customer is hesitating, declining, or what is holding them back (e.g. "Aapko kya cheez rok rahi hai? Timing, fees, ya kuch aur?"), and the customer answers with an objection topic or obstacle (e.g. "timings", "timing", "fees", "price", "paise", "schedule", "offline", "subah nahi ho payega", "busy", "cost"):
        This is the customer ANSWERING the objection question! Set "is_disinterested" = true, set "customer_intent" = "DISINTEREST", and set "question_summary" = null! NEVER classify this as "QUESTION" or as a request to see timings/fees!
    - NEGATIVE GUARD: Do NOT set to true for pure informational questions, timing inquiries, or schedule flexibility questions ("timing kya hai?", "fees kitni hai?", "flexible timings", "subah shaam alag timing ho sakti hai?", "can I change batches?") without an explicit rejection/cancellation signal.

12. "is_complaint_or_refund":
    - boolean: true if customer demands a refund, threatens legal/police action, or accuses fraud/scam.

12b. "is_out_of_scope":
    - boolean: true if the customer's message asks about programming/coding, software code, syntax errors (e.g. Python, JavaScript, Hello World), mathematics, science, homework, medical diagnosis, recipes/cooking, politics, news, trivia, general world knowledge, or treats this bot as a general-purpose AI / search engine (ChatGPT, Siri, Google).
    - CLOSED-WORLD ACADEMY DOMAIN: Sensationz Academy strictly offers Performing Arts & Dance courses (Dance, Yoga, Kathak, Western Dance, Zumba, Music, Guitar, Singing, Acting, Drawing). Anything outside our courses, batches, admissions, trial, app, and fees is strictly OUT OF SCOPE. Otherwise false.

13. "customer_intent":
    Must be EXACTLY ONE of:
    - "OUT_OF_SCOPE": Customer is asking an off-topic or general knowledge question completely unrelated to Sensationz Performing Arts Academy (e.g. coding/programming, syntax errors, recipes, general trivia, homework, jokes, medical advice, politics, general AI tasks). When assigned, set "is_out_of_scope" = true and set "question_summary" = "Off-topic query about [topic]".
    - "QUESTION": Customer is asking an informational question or inquiring about schedule/policies (e.g. "kathak b sikhate ho ?", "fees kitni hai?", "timing kya hai?", "flexible timings", "can I switch batch?", "how to apply?", "what is syllabus?").
      • CRITICAL NEGATIVE GUARD (OBJECTION ANSWERING): If the AI in the recent transcript asked what is holding the customer back, why they are hesitating, or asked if it's timing or fees (e.g. "kya cheez rok rahi hai? Kya timing, fees...?"), and the customer answers by naming an obstacle/topic (e.g. "timings", "timing", "fees", "price", "paise", "cost", "offline", "subah"), this is an OBJECTION REASON ("DISINTEREST"), NOT A "QUESTION"! NEVER output "QUESTION" when the customer is naming what is holding them back!
    - "TIMING_SELECTION": Customer is selecting or stating their preferred batch timing (e.g. "morning 7 am", "shaam 6 baje", "7:00-8:00 AM", "subah ka batch").
    - "TEACHER_SELECTION": Customer is selecting or stating their preferred instructor/teacher (e.g. "madhuri mam", "i want sneha mam", "vibha mittal", "aditi vyas").
    - "DAY_SELECTION": Customer is selecting or stating their preferred batch days (e.g. "tuesday thursday", "weekend batch", "sat sun", "tue/thu", "sat & sun").
    - "PACKAGE_SELECTION": Customer is selecting or stating their preferred package duration (e.g. "3 months", "1 month", "1 year", "3 mahine", "m3", "3m", "m1", "6m", "1y", "3", "1").
      • CRITICAL ADJACENCY RULE: If the AI in the recent transcript asked the customer to choose a package duration (e.g. "Which package duration would you like to choose?", "Aap inme se kaunsa package choose karna chahenge?"), and the customer responds with ANY duration, package shorthand ("m3", "3m", "m1", "6m", "1y", "3 months", "1 month", "1 year"), or package number, this is 100% "PACKAGE_SELECTION"! Set "selected_package" to that package duration! NEVER classify this as "QUESTION"!
    - "APP_LINK_REQUEST": Customer asks to receive or resend the app download link or website link, OR chooses/taps an app store platform button (e.g. "android app", "ios app", "app link", "download link", "play store", "app store", "send link", "link bhejo", "link do", "konsi app", "which app").
    - "COUPON_REQUEST": Customer asks for their coupon code or discount code.
    - "PROFILE_COMPLETED": Customer confirms they completed profile / app setup.
    - "AGENT_REQUEST": Customer explicitly wants human chat agent takeover ("agent", "human", "transfer to agent"). NEVER assign for phone call queries (use "PHONE_CALL_REQUEST"). NEVER assign for general guidance or button taps like "Yes, help me" (use "QUESTION" or "OTHER").
    - "PHONE_CALL_REQUEST": Customer explicitly wants to connect on a phone call, speak over the phone, or asks for phone numbers to call ("call me", "phone pe baat karni hai", "phone number do", "call par baat ho sakti hai", "give number", "want to talk on phone").
    - "DISINTEREST": Customer declines, rejects, or specifies their refusal obstacle/reason. This includes:
      • Explicit rejections: "not interested", "nahi chahiye", "cancel", "no", "nahi lena", "koi b nhi".
      • OBJECTION ANSWERS TO AI INQUIRIES: When the AI asked what is holding them back or why they are hesitating, and the customer names their obstacle (e.g. "timings", "timing", "fees", "price", "paise", "schedule"), this MUST be classified as "DISINTEREST" with is_disinterested = true!
    - "GREETING": Pure greeting without substantive question ("hi", "hello", "good morning").
    - "COMPLAINT": Refund dispute or legal grievance.
    - "PAYMENT_REQUEST": Customer wants to pay online, asks how to pay, asks for UPI QR code, payment scanner, or payment link (e.g. "pay online", "payment qr", "send qr", "scanner bhejo", "payment link", "how to pay", "kahan pay karein", "upi id bhejo").
    - "PAYMENT_CLAIM": Customer states they completed payment, sent a payment screenshot/slip, or asks to verify payment / confirm enrollment ("paid", "payment done", "maine pay kar diya", "screenshot check karo", "paise bhej diye", "fees bhar di", "enrollment confirm karo").
    - "CLASSES_NOT_SHOWING": Customer states that their enrolled/purchased class, batch, or course is not visible in the app, classes not showing, cannot access class, blank app screen, or asks why course is not showing after login/payment ("show nhi hori", "classes nahi dikh rahi", "batch not showing", "not able to access classes", "kaha se join kare", "cannot see my class", "krlia lkn show nahi hori").
    - "OTHER": Casual acknowledgement ("ok", "ok thk h", "thik h", "theek hai", "haan", "yes", "sure", "done", "sahi hai", "hnji", "proceed") or uncategorized.
      • CRITICAL: Casual agreement words without clock numbers MUST be classified as "OTHER", NEVER as "TIMING_SELECTION"! Casual agreement does NOT change, select, or state a timing!

13. "question_summary":
    - Brief 3-8 word English description of what they are asking about if customer_intent is "QUESTION", else null.

14. "detected_language":
    - Detect the customer's exact language and writing script from any language in the world (e.g. "English", "Hinglish (Latin script)", "Hindi (Devanagari script)", "Tamil (Latin script)", "Tamil (Tamil script)", "Spanish", "French", "German", "Arabic", etc.).
    - STRICT SCRIPT RULES — READ CAREFULLY:
      • LATIN/ROMAN ALPHABET = NEVER DEVANAGARI: If the customer's message contains ONLY standard ASCII/Latin characters (a-z, A-Z, 0-9, punctuation, spaces), it is physically impossible for it to be Devanagari script. You MUST classify it as "Hinglish (Latin script)" (if Indic/Hindi meaning) or "English" (if pure English).
      • Hinglish (Latin script): Any message typed in the Latin alphabet that uses Hindi/Urdu words, shortened forms, or phonetic transliterations. Examples: "fees kya h", "teachers kon h", "trial kon leta h", "mjhe tumpe trust nhi hora", "kya timing h", "nhi kathak ki fees bta", "seekhna h", "acha", "fees bta do", "kya h", "kon h", "kab h", "batao", "nahi lena", "thik h", "hnji", "kaise hoga", "mujhe chahiye", "mam", "sir", "plz", "pls". ALL of these MUST be classified as "Hinglish (Latin script)".
      • English: Message is in standard English words with no Hindi/Urdu terms.
      • Classify as "Hindi (Devanagari script)" ONLY IF the customer's message literally contains Devanagari Unicode characters (e.g. "कथक की फीस बताओ", "नमस्ते", "क्या टाइमिंग है"). If you do NOT see actual Devanagari characters (अ आ इ ई etc.) in the message, NEVER output "Hindi (Devanagari script)"!
      • For any other language worldwide, accurately indicate the language and script (e.g. "Tamil (Latin script)", "Tamil (Tamil script)", "Spanish", "Arabic", etc.).

15. "explicit_language_request":
    - string or null: If customer explicitly asks to change language or talk in a specific language (e.g. "talk in Hindi", "speak in English", "Hindi please", "English me bolo", "switch language to Hindi"), specify the target language name (e.g. "Hindi", "English", "Hinglish"). Otherwise null.

16. "wants_resend_video":
    - boolean: true if customer explicitly asks to send or resend a tutorial video or guide (e.g. "video bhejo", "video nahi aayi", "send video again", "video dobara bhejo", "tutorial video", "resend video", "app video"). Otherwise false.
    - CRITICAL NEGATIVE GUARD: Set to FALSE if customer is asking for an INSTRUCTOR DEMO VIDEO, sample class, or teacher preview (e.g. "demo video", "demo", "sample video", "teacher demo", "nidhi demo", "video dikhao", "demo dikhao")! Teacher demo requests are informational queries ("QUESTION"), NOT tutorial video requests!

17. "claims_payment_or_enrollment":
    - boolean: true if customer states they already paid, completed payment, transferred money, sent a payment screenshot/slip, or asks to verify payment / confirm enrollment / confirm admission ("paid", "payment done", "maine payment kar di", "screenshot check karo", "paise bhej diye", "fees bhar di", "enrollment confirm karo", "is my payment received?"). Otherwise false.
    - CRITICAL NEGATIVE GUARD: Set to FALSE if customer is asking HOW to pay, wanting to pay online, or requesting a payment link/QR code (e.g. "pay online", "send qr", "how to pay")! Those are PAYMENT_REQUEST, NOT claims of having completed payment!

18. "wants_app_guide":
    - boolean: true if customer asks for or wants the app guide, app download video, profile setup video, or installation video/tutorial (e.g. "app guide", "app video", "how to download app video", "app video guide", "download video", "how to install app", "app kaise download kare video", "send app guide", "profile video"). Otherwise false.

19. "wants_trial_guide":
    - boolean: true if customer asks for or wants the trial booking video guide or how to book a trial class video/tutorial (e.g. "trial video guide", "trial video", "trial booking video", "how to book trial video", "demo booking video", "trial guide", "trial class video guide"). Otherwise false.

20. "unoffered_type":
    Must be ONE of "TIMING", "PACKAGE", "TEACHER", "COURSE", or null.
    Set when the customer asks for, selects, or mentions an entity NOT available in our course catalog or not offered for the active course:
    - "PACKAGE": Customer mentions an unoffered duration/package (e.g. "2 years", "2 yearly", "5 years", "2 months", "15 days", "6 weeks").
    - "TIMING": Customer mentions an unoffered class hour/batch (e.g. "3", "3pm", "3:00", "11-12", "9pm", "2 to 3").
    - "TEACHER": Customer mentions an instructor not conducting this course (e.g. "Deepanshu", "Rahul").
    - "COURSE": Customer asks for an unoffered course type NOT available in our Allowed Catalog Courses (e.g. "prenatal yoga", "swimming", "karate").
      CRITICAL: If the customer mentions any course or dance style that is in Allowed Catalog Courses (e.g. "ladies dance", "kathak", "western dance", "zumba", "music"), this is an OFFERED catalog course! Set selected_course to that course, and set unoffered_type to null! NEVER set unoffered_type to "COURSE" for an offered catalog course!
    - null: If the requested entity is valid/allowed or none was mentioned.

21. "unoffered_value":
    - string or null: The exact unoffered entity value mentioned by the customer (e.g. "2 Years", "3:00 PM", "Deepanshu", "Prenatal Yoga").

22. "unoffered_timing_mentioned":
    - string or null: If customer requested or mentioned an unoffered timing (e.g. "11-12", "3 pm", "3:00"), extract that timing string here. Otherwise null.

21. "cannot_access_classes":
    - boolean: true if customer states that their course/batch/class is not showing or not visible in the app, or they are unable to see or access their enrolled classes after login or payment ("show nhi hori", "krlia lkn classes show nhi hori", "classes nahi dikh rahi", "batch not showing", "not able to access classes", "kaha se join kare", "cannot see my class"). Otherwise false.

22. "conversation_summary":
    - string or null: A concise 1-2 sentence factual English summary of the customer's conversational journey so far based on the chronological transcript. Note what was offered, what was selected, any rejections or objections (e.g. "Customer rejected the available 10 batch timings due to a timing conflict."), and what the customer's current stance is.

RESPOND ONLY WITH STRICT JSON matching this schema:
{
  "app_installed_stated": "confirmed" | "denied" | "unmentioned",
  "profile_created_stated": "confirmed" | "denied" | "unmentioned",
  "selected_timing": string | null,
  "requested_start_24h": string | null,
  "requested_end_24h": string | null,
  "requested_timing_period": string | null,
  "timing_period": string | null,
  "selected_teacher": string | null,
  "selected_days": string | null,
  "selected_package": string | null,
  "selected_course": string | null,
  "wants_trial": boolean,
  "wants_demo_video": boolean,
  "wants_app_link": boolean,
  "requests_qr": boolean,
  "requests_human_agent": boolean,
  "wants_phone_call": boolean,
  "is_coupon_requested": boolean,
  "is_disinterested": boolean,
  "is_complaint_or_refund": boolean,
  "customer_intent": string,
  "question_summary": string | null,
  "detected_language": string,
  "explicit_language_request": string | null,
  "wants_resend_video": boolean,
  "claims_payment_or_enrollment": boolean,
  "cannot_access_classes": boolean,
  "timing_ambiguous": boolean,
  "wants_app_guide": boolean,
  "wants_trial_guide": boolean,
  "unoffered_type": "TIMING" | "PACKAGE" | "TEACHER" | "COURSE" | null,
  "unoffered_value": string | null,
  "unoffered_timing_mentioned": string | null,
  "conversation_summary": string | null
}"""


async def extract_intent_and_state_changes(
    text: str,
    state: Optional[Dict[str, Any]] = None,
    history: Optional[list] = None,
) -> Dict[str, Any]:
    """
    Executes the silent Extractor call.
    Returns structured dictionary with semantic state changes and intent.
    Falls back to _empty_flags() on any failure.
    """
    if not text or not text.strip():
        return _empty_flags()

    s = state or {}

    # Build chronological interleaved transcript from recent conversation history (last 8 turns)
    transcript_turns = []
    if history:
        for turn in history[-8:]:
            role = "Customer" if turn.get("role") in ("user", "customer") else "AI"
            content = (turn.get("content") or "").strip()
            if content:
                clean_content = content[:400] + ("..." if len(content) > 400 else "")
                transcript_turns.append(f"[{role}]: \"{clean_content}\"")
    
    interleaved_transcript = "\n".join(transcript_turns) if transcript_turns else "None (New conversation)"

    from src.core import kb_loader
    from src.core.helpers import (
        get_available_timings,
        get_course_instructors,
        resolve_canonical_teacher,
        get_available_days_for_batch,
        normalize_day_string,
        get_day_code,
    )
    active_cid = s.get("course") or kb_loader.detect_course_from_text(text, default_course=None) or ""
    course_pkgs = list(kb_loader.get_valid_packages(active_cid).keys()) if active_cid else ["1 Month", "3 Months", "6 Months", "1 Year"]
    pkgs_str = ", ".join(f'"{p}"' for p in course_pkgs) if course_pkgs else '"1 Month", "3 Months", "6 Months", "1 Year"'

    # Canonical teachers for active course (or all catalog courses if course not yet known)
    course_teachers = get_course_instructors(active_cid if active_cid else None)
    teachers_str = ", ".join(f'"{t}"' for t in course_teachers) if course_teachers else '"Check Course Batches"'

    # Canonical timings for active course (includes all course batches so customer requests are properly recognized)
    course_batches = [b.get("time_12h", "").strip() for b in get_available_timings(active_cid)] if active_cid else []
    timings_str = ", ".join(f'"{t}"' for t in course_batches if t) if course_batches else '"Check Course Batches"'

    # Canonical days for active course
    course_days = get_available_days_for_batch(active_cid if active_cid else None)
    days_str = ", ".join(f'"{d}"' for d in course_days) if course_days else '"Check Course Batches"'

    catalog_courses = [item.get("name") for item in kb_loader.get_course_catalog() if item.get("name")]
    catalog_str = ", ".join(f'"{c}"' for c in catalog_courses)

    amb_hr_ctx = s.get("ambiguous_hour") or ""
    context_prompt = (
        f"CUSTOMER SESSION CONTEXT:\n"
        f"- Current Stage: {s.get('stage', 'NEW')}\n"
        f"- Refusal / Objection Stage: {s.get('refusal_stage', 0)}\n"
        f"- Active Course: {s.get('course') or 'None (Not Selected)'}\n"
        f"- Pending Ambiguous Timing Hour: {f'{amb_hr_ctx} (Awaiting Morning vs Evening preference)' if amb_hr_ctx else 'None'}\n"
        f"- Allowed Catalog Courses: [{catalog_str}]\n"
        f"- Allowed Canonical Batch Timings for Active Course: [{timings_str}]\n"
        f"- Allowed Canonical Batch Days for Active Course: [{days_str}]\n"
        f"- Allowed Canonical Packages for Active Course: [{pkgs_str}]\n"
        f"- Allowed Canonical Instructors for Active Course: [{teachers_str}]\n"
        f"- Selected Package: {s.get('package') or s.get('selected_duration') or 'None'}\n"
        f"- Selected Timing: {s.get('timing') or s.get('selected_timing') or 'None'}\n"
        f"- Selected Teacher: {s.get('selected_teacher') or 'None'}\n"
        f"- Selected Days: {s.get('days') or s.get('selected_days') or 'None'}\n"
        f"- App Installed in State: {bool(s.get('app_installed'))}\n"
        f"- Profile Created in State: {bool(s.get('profile_created'))}\n"
        f"- Welcome Coupon Already Sent: {bool(s.get('coupon_sent'))}\n\n"
        f"RECENT CONVERSATION (Chronological Order):\n"
        f"{interleaved_transcript}\n\n"
        f"LATEST CUSTOMER MESSAGE:\n\"{text.strip()}\""
    )

    try:
        llm = _get_classifier_llm()
        response = await llm.ainvoke([
            SystemMessage(content=EXTRACTOR_SYSTEM_PROMPT),
            HumanMessage(content=context_prompt),
        ])
        raw = response.content.strip().strip("`").replace("json\n", "").strip()
        data = json.loads(raw)

        # Normalize and validate outputs against fallback defaults
        clean = _empty_flags()
        for k in clean:
            if k in data:
                clean[k] = data[k]

        # Ensure strict typing on boolean flags
        for bool_key in [
            "wants_trial", "wants_demo_video", "wants_app_link", "requests_qr",
            "requests_human_agent", "wants_phone_call", "is_coupon_requested", "is_disinterested",
            "is_complaint_or_refund", "is_out_of_scope", "wants_resend_video", "claims_payment_or_enrollment",
            "cannot_access_classes", "wants_demo", "wants_app_guide", "wants_app_video_guide",
            "wants_trial_guide", "wants_trial_video_guide"
        ]:
            val = clean.get(bool_key)
            if isinstance(val, str):
                clean[bool_key] = val.lower() in ("true", "1", "yes")
            else:
                clean[bool_key] = bool(val)

        clean["wants_app_guide"] = bool(clean.get("wants_app_guide") or data.get("wants_app_video_guide"))
        clean["wants_app_video_guide"] = clean["wants_app_guide"]
        clean["wants_trial_guide"] = bool(clean.get("wants_trial_guide") or data.get("wants_trial_video_guide"))
        clean["wants_trial_video_guide"] = clean["wants_trial_guide"]

        # Sync semantic request flags with customer_intent without raw-text regex overrides
        if clean.get("customer_intent") == "OUT_OF_SCOPE":
            clean["is_out_of_scope"] = True
        elif clean.get("is_out_of_scope") and clean.get("customer_intent") not in ("DISINTEREST", "COMPLAINT", "AGENT_REQUEST", "PHONE_CALL_REQUEST"):
            clean["customer_intent"] = "OUT_OF_SCOPE"

        if clean.get("customer_intent") == "PHONE_CALL_REQUEST":
            clean["wants_phone_call"] = True
        elif clean.get("wants_phone_call") and clean.get("customer_intent") not in ("DISINTEREST", "COMPLAINT", "AGENT_REQUEST"):
            clean["customer_intent"] = "PHONE_CALL_REQUEST"

        if clean.get("wants_phone_call"):
            clean["requests_human_agent"] = False

        if clean.get("customer_intent") == "PAYMENT_REQUEST":
            clean["requests_qr"] = True
        elif clean.get("requests_qr") and clean.get("customer_intent") not in ("DISINTEREST", "COMPLAINT", "AGENT_REQUEST", "PHONE_CALL_REQUEST"):
            clean["customer_intent"] = "PAYMENT_REQUEST"

        if clean.get("customer_intent") == "APP_LINK_REQUEST":
            clean["wants_app_link"] = True
        elif clean.get("wants_app_link") and clean.get("customer_intent") not in ("DISINTEREST", "COMPLAINT", "AGENT_REQUEST", "PHONE_CALL_REQUEST"):
            clean["customer_intent"] = "APP_LINK_REQUEST"

        # Teacher demo inquiries must never trigger tutorial or install video guides
        if clean.get("wants_demo_video") or any(w in text.lower() for w in ["demo", "sample", "preview", "teaching style"]):
            clean["wants_demo"] = True
            clean["wants_resend_video"] = False
            clean["wants_app_guide"] = False
            clean["wants_app_video_guide"] = False
            clean["wants_trial_guide"] = False
            clean["wants_trial_video_guide"] = False

        # Validate selected timing against allowed canonical course batches (supports button text e.g. "5:00 PM", and casual formats e.g. "12pm", "12-1")
        is_timing_settled = bool(
            s.get("payment_status") == "PAID"
            or s.get("is_enrolled")
            or s.get("timing")
            or s.get("awaiting_email_fulfillment")
        )

        # Check if the user's actual raw text is a colloquial bare hour (e.g. '5bje', '5 baje', '5', '7bje')
        # without explicit meridiem (AM/PM/subah/shaam). This prevents the LLM classifier from prematurely
        # guessing a specific AM/PM slot and bypassing morning vs evening ambiguity check.
        u_raw = text.strip().lower()
        u_clean = u_raw.replace(" ", "").replace("-", "to")
        u_has_am = any(w in u_raw for w in ["am", "morning", "subah", "sawere", "savere", "mrng", "morng"])
        u_has_pm = any(w in u_raw for w in ["pm", "evening", "shaam", "sham", "night", "raat", "dopahar", "afternoon"])

        colloquial_text_match = (
            re.search(r"^(\d{1,2})(?::00)?(?:to\d{1,2}(?::00)?)?(?:bje|baje|o'?clock)?$", u_clean)
            or re.search(r"\b(\d{1,2})\s*(?:bje|baje|baze|o'?clock)\b", u_raw)
        )
        is_user_bare_hour = (
            not is_timing_settled
            and not (u_has_am or u_has_pm)
            and not clean.get("selected_package")
            and clean.get("customer_intent") not in ("DISINTEREST", "COMPLAINT", "AGENT_REQUEST", "PHONE_CALL_REQUEST", "PACKAGE_SELECTION")
            and bool(colloquial_text_match or (u_clean.isdigit() and 1 <= int(u_clean) <= 12))
        )

        matched_batch = None
        clean_user = ""
        raw_timing = None

        if is_user_bare_hour:
            target_hr = int(colloquial_text_match.group(1)) if colloquial_text_match else int(u_clean)
            clean_user = str(target_hr)
            candidates = []
            for b in course_batches:
                try:
                    start_hr = int(b.split(":")[0])
                    if start_hr == target_hr:
                        candidates.append(b)
                except Exception:
                    pass
            candidates = list(dict.fromkeys(candidates))
            has_am = any("am" in c.lower() for c in candidates)
            has_pm = any("pm" in c.lower() for c in candidates)

            if len(candidates) == 1 or (not (has_am and has_pm) and candidates):
                matched_batch = candidates[0]
            elif len(candidates) > 1 and has_am and has_pm:
                period_hint = (s.get("timing_period") or "").lower()
                if period_hint in ("evening", "afternoon", "pm"):
                    pm_cands = [c for c in candidates if "pm" in c.lower()]
                    if pm_cands:
                        matched_batch = pm_cands[0]
                elif period_hint in ("morning", "am"):
                    am_cands = [c for c in candidates if "am" in c.lower()]
                    if am_cands:
                        matched_batch = am_cands[0]

                if not matched_batch:
                    clean["timing_ambiguous"] = True
                    clean["ambiguous_hour"] = target_hr
                    clean["ambiguous_timing_candidates"] = candidates
                    clean["selected_timing"] = None
                    clean["unoffered_type"] = None
                    clean["unoffered_value"] = None
                    clean["unoffered_timing_mentioned"] = None
                    clean["customer_intent"] = "TIMING_SELECTION"
                    clean["question_summary"] = f"Clarify morning vs evening for {target_hr} o'clock"
        else:
            raw_timing = clean.get("selected_timing") or clean.get("unoffered_timing_mentioned")
            if not raw_timing and not clean.get("selected_package") and clean.get("customer_intent") not in ("DISINTEREST", "COMPLAINT", "AGENT_REQUEST", "PHONE_CALL_REQUEST", "PACKAGE_SELECTION"):
                if not is_timing_settled:
                    raw_timing = text.strip()

            if raw_timing and isinstance(raw_timing, str):
                r_low = raw_timing.strip().lower()
                clean_user = r_low.replace(" ", "").replace("-", "to")

                # Colloquial bare hour resolution (e.g. '7bje', '7 baje', '7', '6 baje', '5')
                colloquial_hr_match = re.search(r"^(\d{1,2})(?:bje|baje|o'?clock)?$", clean_user) or re.search(r"\b(\d{1,2})\s*(?:bje|baje|baze)\b", r_low)
                if colloquial_hr_match or clean_user.isdigit():
                    target_hr = int(colloquial_hr_match.group(1)) if colloquial_hr_match else int(clean_user)
                    candidates = []
                    for b in course_batches:
                        try:
                            start_hr = int(b.split(":")[0])
                            if start_hr == target_hr:
                                candidates.append(b)
                        except Exception:
                            pass
                    candidates = list(dict.fromkeys(candidates))
                    has_am = any("am" in c.lower() for c in candidates)
                    has_pm = any("pm" in c.lower() for c in candidates)

                    if len(candidates) == 1 or (not (has_am and has_pm) and candidates):
                        # Unambiguous single slot or all candidates belong to the same period (e.g. evening-only or morning-only course)
                        matched_batch = candidates[0]
                    elif len(candidates) > 1 and has_am and has_pm:
                        period_hint = (s.get("timing_period") or "").lower()
                        if not period_hint:
                            if any(w in text.lower() for w in ["evening", "sham", "shaam", "raat", "night", "pm", "afternoon", "dopahar"]):
                                period_hint = "evening"
                            elif any(w in text.lower() for w in ["morning", "subah", "am"]):
                                period_hint = "morning"
                        if period_hint in ("evening", "afternoon", "pm"):
                            pm_cands = [c for c in candidates if "pm" in c.lower()]
                            if pm_cands:
                                matched_batch = pm_cands[0]
                        elif period_hint in ("morning", "am"):
                            am_cands = [c for c in candidates if "am" in c.lower()]
                            if am_cands:
                                matched_batch = am_cands[0]
                        
                        if not matched_batch:
                            # Genuinely ambiguous hour across morning and evening (e.g. 7 AM vs 7 PM both exist in Yoga)
                            clean["timing_ambiguous"] = True
                            clean["ambiguous_hour"] = target_hr
                            clean["ambiguous_timing_candidates"] = candidates
                            clean["selected_timing"] = None
                            clean["unoffered_type"] = None
                            clean["unoffered_value"] = None
                            clean["unoffered_timing_mentioned"] = None
                            clean["customer_intent"] = "TIMING_SELECTION"
                            clean["question_summary"] = f"Clarify morning vs evening for {target_hr} o'clock"

                if not matched_batch and not clean.get("timing_ambiguous"):
                    clean_u_compact = clean_user.replace("–", "to").replace(":00", "")
                    for b in course_batches:
                        b_low = b.lower()
                        norm_b = b_low.replace("–", " to ").replace("-", " to ")
                        if r_low == b_low or r_low == norm_b:
                            matched_batch = b
                            break
                        # Match start hour/meridiem: e.g. '12pm' -> '12:00 PM', '5pm' -> '5:00 PM'
                        start_part = norm_b.split(" to ")[0].replace(":00", "").replace(" ", "")
                        if clean_u_compact == start_part or clean_user == start_part:
                            matched_batch = b
                            break
                        # Match full range: e.g. '12to1' or '12pmto1pm'
                        clean_batch = norm_b.replace(":00", "").replace(" ", "")
                        if clean_u_compact == clean_batch or clean_u_compact == clean_batch.replace("pm", "").replace("am", "") or clean_user == clean_batch:
                            matched_batch = b
                            break

        if matched_batch:
            clean["selected_timing"] = matched_batch
            clean["timing_ambiguous"] = False
            clean["ambiguous_hour"] = None
            clean["ambiguous_timing_candidates"] = None
            clean["timing_resolution_status"] = "SELECTED"
            clean["timing_selection_attempted"] = True
            clean["unoffered_timing_mentioned"] = None
            if clean.get("unoffered_type") == "TIMING":
                clean["unoffered_type"] = None
                clean["unoffered_value"] = None
            clean["is_disinterested"] = False
            if clean.get("customer_intent") in ("QUESTION", "COURSE_INQUIRY", "DISINTEREST", "OTHER", None):
                clean["customer_intent"] = "TIMING_SELECTION"
        else:
            clean["selected_timing"] = None

        # Period Detection: Trust LLM brain extraction first, fallback to basic keywords
        detected_period = clean.get("timing_period")
        if not detected_period:
            if re.search(r"\b(morning|subah|sbh|subha|sawere|savere|mrng|early morning)\b", text, re.IGNORECASE):
                detected_period = "Morning"
            elif re.search(r"\b(evening|shaam|sham|night|raat)\b", text, re.IGNORECASE):
                detected_period = "Evening"
            elif re.search(r"\b(afternoon|dopahar)\b", text, re.IGNORECASE):
                detected_period = "Afternoon"

        if detected_period:
            clean["timing_period"] = detected_period
            clean["is_disinterested"] = False
            if clean.get("customer_intent") in ("QUESTION", "COURSE_INQUIRY", "DISINTEREST", "OTHER", None):
                clean["customer_intent"] = "TIMING_SELECTION"

            # If previous state had an unresolved ambiguous timing, settle it with the detected period!
            amb_candidates = s.get("ambiguous_timing_candidates") or []
            if not amb_candidates and s.get("ambiguous_hour"):
                try:
                    t_hr = int(s["ambiguous_hour"])
                    for b in course_batches:
                        try:
                            if int(b.split(":")[0]) == t_hr:
                                amb_candidates.append(b)
                        except Exception:
                            pass
                    amb_candidates = list(dict.fromkeys(amb_candidates))
                except Exception:
                    pass

            if (s.get("timing_ambiguous") or s.get("ambiguous_hour")) and amb_candidates:
                period_low = (detected_period or "").lower()
                if period_low in ("morning", "am", "sbh", "subah", "sawere", "savere", "mrng"):
                    m_c = [c for c in amb_candidates if "am" in c.lower()]
                    if m_c:
                        clean["selected_timing"] = m_c[0]
                        clean["timing_ambiguous"] = False
                        clean["ambiguous_hour"] = None
                        clean["ambiguous_timing_candidates"] = None
                        clean["timing_resolution_status"] = "SELECTED"
                        clean["timing_selection_attempted"] = True
                elif period_low in ("evening", "afternoon", "pm", "shaam", "sham", "raat"):
                    m_c = [c for c in amb_candidates if "pm" in c.lower()]
                    if m_c:
                        clean["selected_timing"] = m_c[0]
                        clean["timing_ambiguous"] = False
                        clean["ambiguous_hour"] = None
                        clean["ambiguous_timing_candidates"] = None
                        clean["timing_resolution_status"] = "SELECTED"
                        clean["timing_selection_attempted"] = True

        # The raw customer message is authoritative for timing. The extractor
        # may suggest a canonical slot, but it cannot override an explicit
        # range/period that does not exist in the active course catalog.
        from src.core.helpers.schedule_engine import resolve_catalog_batch_semantics
        raw_timing_preview = resolve_catalog_batch_semantics(active_cid, raw_text=text)
        raw_timing_attempt = raw_timing_preview.get("status") != "NONE"
        AFFIRMATIVE_AGREEMENT_REGEX = re.compile(
            r"^(?:ok|okay|thk|thik|theek|sahi|done|hnji|haan|ha|yes|yep|sure|fine|alright|cool|proceed|perfect|chalega|chlega)"
            r"(?:\s+(?:hai|h|bhai|ji|sir|mam|ok|okay|thk|theek|sahi|kardo|krdo|done|sure|yes|chalega|chlega))*"
            r"[.!?\s]*$",
            re.IGNORECASE,
        )
        is_casual_agreement = bool(AFFIRMATIVE_AGREEMENT_REGEX.match(text.strip()))
        has_time_num = bool(re.search(r'(?<!\d)\d{1,2}(?::\d{2}\s*(?:am|pm)?|\s*(?:am|pm|bje|baje|o\'clock))\b', text, re.IGNORECASE))
        has_bare_time_word = bool(re.search(r'\b(morning|subah|subha|sawere|savere|mrng|evening|shaam|sham|raat|dopahar|afternoon|sbh)\b', text, re.IGNORECASE))
        # WhatsApp shorthand: "10ok", "10done", and "10 book krdo".
        # The catalog resolver remains authoritative; this only opens the
        # timing path so those messages are not discarded before validation.
        timing_stage = s.get("stage") in {
            "DAYS_SELECTED", "ENROLL_ASKED", "TIMING_SELECTED",
            "TEACHER_SELECTED", "PACKAGE_SELECTED"
        }
        has_bare_hour = bool(
            timing_stage
            and re.search(r'(?<!\d)\d{1,2}(?!\d)', text)
        )
        package_candidate = bool(clean.get("selected_package") or clean.get("customer_intent") == "PACKAGE_SELECTION")

        if is_casual_agreement and not has_time_num and not has_bare_time_word:
            if clean.get("customer_intent") == "TIMING_SELECTION":
                clean["customer_intent"] = "OTHER"
            clean["selected_timing"] = None
            clean["timing_ambiguous"] = False
            clean["ambiguous_hour"] = None
            clean["ambiguous_timing_candidates"] = None
            clean["timing_resolution_status"] = "NONE"
            clean["timing_selection_attempted"] = False
            timing_requested = False
        elif (
            clean.get("selected_timing")
            and not raw_timing_attempt
            and (clean.get("timing_resolution_status") == "SELECTED" or clean.get("selected_timing") in course_batches)
        ):
            timing_requested = False
        else:
            timing_requested = bool(
                clean.get("requested_start_24h")
                or clean.get("requested_end_24h")
                or clean.get("requested_timing_period")
                or clean.get("customer_intent") == "TIMING_SELECTION"
                or (clean.get("selected_timing") and not clean.get("question_summary"))
                or has_time_num
                or has_bare_hour
                or has_bare_time_word
                or raw_timing_attempt
            )
            if package_candidate and not (has_time_num or has_bare_time_word):
                timing_requested = False
        if timing_requested:
            timing_resolution = resolve_catalog_batch_semantics(
                active_cid,
                start_24h=clean.get("requested_start_24h"),
                end_24h=clean.get("requested_end_24h"),
                period=clean.get("timing_period") or clean.get("requested_timing_period"),
                raw_text=text,
            )
            resolution_status = timing_resolution["status"]
            clean["timing_resolution_status"] = resolution_status
            clean["timing_selection_attempted"] = resolution_status != "NONE"

            if resolution_status == "SELECTED":
                clean["selected_timing"] = timing_resolution["timing"]
                clean["timing_ambiguous"] = False
                clean["ambiguous_hour"] = None
                clean["ambiguous_timing_candidates"] = None
                clean["unoffered_type"] = None
                clean["unoffered_value"] = None
                clean["unoffered_timing_mentioned"] = None
            elif resolution_status == "AMBIGUOUS":
                clean["selected_timing"] = None
                clean["timing_ambiguous"] = True
                clean["ambiguous_timing_candidates"] = timing_resolution.get("candidates") or None
                clean["unoffered_type"] = None
                clean["unoffered_value"] = None
                clean["unoffered_timing_mentioned"] = None
                clean["customer_intent"] = "TIMING_SELECTION"
            elif resolution_status in {"MULTIPLE_VALID", "MIXED"}:
                # Several requests must never be reduced to the first/last
                # number. The prompt layer asks the customer to choose one.
                clean["selected_timing"] = None
                clean["timing_ambiguous"] = True
                clean["ambiguous_hour"] = None
                clean["ambiguous_timing_candidates"] = timing_resolution.get("candidates") or None
                clean["invalid_timing_candidates"] = timing_resolution.get("invalid") or None
                clean["unoffered_type"] = None
                clean["unoffered_value"] = None
                clean["unoffered_timing_mentioned"] = None
                clean["customer_intent"] = "TIMING_SELECTION"
            elif resolution_status == "PERIOD_ONLY":
                clean["selected_timing"] = None
                clean["timing_ambiguous"] = False
                clean["ambiguous_timing_candidates"] = timing_resolution.get("candidates") or None
                clean["customer_intent"] = "TIMING_SELECTION"
            elif resolution_status == "UNAVAILABLE":
                clean["selected_timing"] = None
                clean["timing_ambiguous"] = False
                clean["unoffered_type"] = "TIMING"
                clean["unoffered_value"] = text.strip()
                clean["unoffered_timing_mentioned"] = text.strip()
                clean["customer_intent"] = "QUESTION"
            elif resolution_status == "UNRESOLVED":
                clean["selected_timing"] = None
                clean["timing_ambiguous"] = False
                clean["customer_intent"] = "TIMING_SELECTION"

        # Strict canonical package normalization against allowed course packages
        from src.core.coupons import detect_unoffered_duration
        unoffered_dur = detect_unoffered_duration(text, course_id=active_cid or "yoga", allowed_packages=course_pkgs)
        if unoffered_dur:
            clean["unoffered_type"] = "PACKAGE"
            clean["unoffered_value"] = unoffered_dur
            clean["selected_package"] = None
            if clean.get("customer_intent") not in ("DISINTEREST", "COMPLAINT"):
                clean["customer_intent"] = "QUESTION"
                clean["question_summary"] = f"Inquiry about {unoffered_dur} package"
        elif clean.get("unoffered_type") == "PACKAGE":
            from src.core.coupons import normalize_package_name
            cand_pkg = normalize_package_name(clean.get("unoffered_value") or clean.get("selected_package"), course_id=active_cid or "yoga")
            if cand_pkg and cand_pkg in course_pkgs:
                clean["unoffered_type"] = None
                clean["unoffered_value"] = None
                clean["selected_package"] = cand_pkg
                clean["customer_intent"] = "PACKAGE_SELECTION"
            else:
                clean["selected_package"] = None
                if clean.get("customer_intent") not in ("DISINTEREST", "COMPLAINT"):
                    clean["customer_intent"] = "QUESTION"
        else:
            raw_pkg = clean.get("selected_package")
            if raw_pkg and isinstance(raw_pkg, str):
                from src.core.coupons import normalize_package_name
                norm_pkg = normalize_package_name(raw_pkg, course_id=active_cid or "yoga")
                clean["selected_package"] = norm_pkg or (raw_pkg if raw_pkg in course_pkgs else None)

        # Universal Semantic Overrides: Active slot selections strictly negate disinterest
        raw_text_course = kb_loader.detect_course_from_text(text, default_course=None)
        from src.core.coupons import detect_package_from_text
        text_teacher = resolve_canonical_teacher(text, course_id=active_cid or None)
        text_pkg = detect_package_from_text(text, course_id=active_cid or "yoga")
        if text_pkg and not clean.get("selected_package"):
            clean["selected_package"] = text_pkg

        has_pkg_question = bool(re.search(r"(\?|kya|kitn[aei]|how much|cost|fees?|what|when|where|kyu|why)", text, re.IGNORECASE))
        if text_pkg and not has_pkg_question and clean.get("customer_intent") not in ("DISINTEREST", "COMPLAINT"):
            clean["is_disinterested"] = False
            clean["customer_intent"] = "PACKAGE_SELECTION"
        text_timing_mentioned = bool(
            re.search(r"\b(\d{1,2}(?::\d{2})?\s*(?:am|pm)|\d{1,2}\s*to\s*\d{1,2}|morning|subah|evening|shaam|sham|dopahar|afternoon|night|raat)\b", text, re.IGNORECASE)
        )
        has_contrastive_slot_in_text = bool(
            (clean.get("selected_timing") and text_timing_mentioned)
            or (clean.get("selected_teacher") and text_teacher)
            or (clean.get("selected_package") and text_pkg)
            or (clean.get("selected_course") and raw_text_course)
        )

        if data.get("is_disinterested") or data.get("customer_intent") == "DISINTEREST":
            if has_contrastive_slot_in_text:
                clean["is_disinterested"] = False
                if clean.get("selected_timing"):
                    clean["customer_intent"] = "TIMING_SELECTION"
                elif clean.get("selected_package"):
                    clean["customer_intent"] = "PACKAGE_SELECTION"
                elif clean.get("selected_teacher"):
                    clean["customer_intent"] = "TEACHER_SELECTION"
                elif clean.get("selected_course"):
                    clean["customer_intent"] = "COURSE_SELECTION"
            else:
                clean["is_disinterested"] = True
                clean["customer_intent"] = "DISINTEREST"
                clean["selected_timing"] = None
                clean["selected_teacher"] = None
                clean["selected_package"] = None
        else:
            if clean.get("selected_timing") or clean.get("selected_teacher") or clean.get("selected_package") or (clean.get("selected_course") and raw_text_course):
                clean["is_disinterested"] = False
                if clean.get("customer_intent") in ("OTHER", None):
                    if clean.get("selected_timing"):
                        clean["customer_intent"] = "TIMING_SELECTION"
                    elif clean.get("selected_teacher"):
                        clean["customer_intent"] = "TEACHER_SELECTION"
                    elif clean.get("selected_package"):
                        clean["customer_intent"] = "PACKAGE_SELECTION"
                    elif clean.get("selected_course"):
                        clean["customer_intent"] = "COURSE_SELECTION"

        # Strict canonical teacher normalization against allowed course teachers
        raw_teacher = clean.get("selected_teacher")
        matched_teacher = None
        if raw_teacher and isinstance(raw_teacher, str):
            matched_teacher = resolve_canonical_teacher(raw_teacher, course_id=active_cid or None)
        if not matched_teacher and clean.get("customer_intent") not in ("DISINTEREST", "COMPLAINT"):
            # Check if user named an instructor in text (including when asking question or requesting demo)
            matched_teacher = resolve_canonical_teacher(text, course_id=active_cid or None)

        is_student_settled = bool(
            s.get("payment_status") == "PAID"
            or s.get("is_enrolled")
            or s.get("awaiting_email_fulfillment")
        )
        if is_student_settled:
            # Paid/enrolled students cannot have random handles or queries treated as unoffered slots
            if clean.get("unoffered_type") in ("TEACHER", "TIMING", "PACKAGE"):
                clean["unoffered_type"] = None
                clean["unoffered_value"] = None
                clean["unoffered_timing_mentioned"] = None
        elif clean.get("unoffered_type") == "TEACHER":
            clean["selected_teacher"] = None
            if clean.get("customer_intent") not in ("DISINTEREST", "COMPLAINT"):
                clean["customer_intent"] = "QUESTION"
        elif raw_teacher and not matched_teacher and course_teachers:
            clean["unoffered_type"] = "TEACHER"
            clean["unoffered_value"] = raw_teacher
            clean["selected_teacher"] = None
            if clean.get("customer_intent") not in ("DISINTEREST", "COMPLAINT"):
                clean["customer_intent"] = "QUESTION"
                clean["question_summary"] = f"Inquiry about instructor {raw_teacher}"
        else:
            clean["selected_teacher"] = matched_teacher or (raw_teacher if raw_teacher in course_teachers else None)
            if clean.get("selected_teacher") and clean.get("customer_intent") in ("QUESTION", "OTHER") and not clean.get("wants_demo"):
                clean["customer_intent"] = "TEACHER_SELECTION"

# Sync unoffered timing mentioned with unoffered_value
        if clean.get("unoffered_type") == "TIMING" and not clean.get("unoffered_timing_mentioned"):
            clean["unoffered_timing_mentioned"] = clean.get("unoffered_value")
        elif clean.get("unoffered_timing_mentioned") and not clean.get("unoffered_type"):
            clean["unoffered_type"] = "TIMING"
            clean["unoffered_value"] = clean.get("unoffered_timing_mentioned")

        # Guard: Unoffered timing MUST have a digit (e.g. '3', '3pm', '11-12'). General words like 'timings', 'ladies timings', or 'schedule' are schedule inquiries, NOT unoffered timings!
        if clean.get("unoffered_type") == "TIMING":
            t_val = str(clean.get("unoffered_value") or clean.get("unoffered_timing_mentioned") or "")
            if not any(char.isdigit() for char in t_val):
                clean["unoffered_type"] = None
                clean["unoffered_value"] = None
                clean["unoffered_timing_mentioned"] = None
                if clean.get("customer_intent") not in ("DISINTEREST", "COMPLAINT"):
                    clean["customer_intent"] = "QUESTION"
                    clean["question_summary"] = "Inquiry about batch timings"

        # Strict canonical day normalization against allowed course days
        raw_day = clean.get("selected_days")
        matched_day = None
        if raw_day and isinstance(raw_day, str):
            from src.core.helpers.schedule_engine import normalize_day_string
            matched_day = normalize_day_string(raw_day)
        elif not raw_day and clean.get("customer_intent") not in ("QUESTION", "DISINTEREST", "COMPLAINT"):
            from src.core.helpers.schedule_engine import normalize_day_string
            matched_day = normalize_day_string(text)

        clean["selected_days"] = matched_day
        if matched_day and clean.get("customer_intent") in ("QUESTION", "OTHER"):
            clean["customer_intent"] = "DAY_SELECTION"

        # Deterministic course detection from user text overrides ambiguous LLM extraction
        llm_course = clean.get("selected_course")
        valid_llm_course = None
        if llm_course:
            entry = kb_loader.get_catalog_entry(str(llm_course))
            if entry:
                valid_llm_course = entry["id"]

        text_course = kb_loader.detect_course_from_text(text, default_course=None)
        if not text_course and history:
            for turn in reversed(history[-4:]):
                if turn.get("role") in ("user", "customer"):
                    c = kb_loader.detect_course_from_text(turn.get("content") or "", default_course=None)
                    if c:
                        text_course = c
                        break

        chosen_course = text_course or valid_llm_course
        if chosen_course:
            clean["selected_course"] = chosen_course
            # If a valid catalog course is identified, it is OFFERED - clear any unoffered course flag!
            if clean.get("unoffered_type") == "COURSE":
                clean["unoffered_type"] = None
                clean["unoffered_value"] = None

        # Guard: If unoffered_type was set to COURSE, check if the value matches ANY course in our catalog
        if clean.get("unoffered_type") == "COURSE":
            u_course = str(clean.get("unoffered_value") or "").strip()
            cat_entry = kb_loader.detect_course_from_text(u_course, default_course=None) or kb_loader.get_catalog_entry(u_course)
            if cat_entry:
                c_id = cat_entry["id"] if isinstance(cat_entry, dict) else cat_entry
                clean["selected_course"] = c_id
                clean["unoffered_type"] = None
                clean["unoffered_value"] = None
            elif clean.get("selected_course"):
                clean["unoffered_type"] = None
                clean["unoffered_value"] = None

        # Final timing authority: legacy extractor normalization above may
        # reinterpret malformed alternatives (for example, "56" as 5 PM).
        # Re-validate the original message after all legacy paths and make
        # the catalog resolver's decision immutable for this turn.
        from src.core.helpers.schedule_engine import resolve_catalog_batch_semantics
        final_timing = (
            resolve_catalog_batch_semantics(active_cid, raw_text=text)
            if timing_requested else {"status": "NONE"}
        )
        final_status = final_timing.get("status")
        if final_status in {"MULTIPLE_VALID", "MIXED"}:
            clean["selected_timing"] = None
            clean["timing_ambiguous"] = True
            clean["ambiguous_hour"] = None
            clean["ambiguous_timing_candidates"] = final_timing.get("candidates") or None
            clean["invalid_timing_candidates"] = final_timing.get("invalid") or None
            clean["unoffered_type"] = None
            clean["unoffered_value"] = None
            clean["unoffered_timing_mentioned"] = None
            clean["timing_resolution_status"] = final_status
            clean["timing_selection_attempted"] = True
            clean["customer_intent"] = "TIMING_SELECTION"
        elif final_status == "SELECTED":
            clean["selected_timing"] = final_timing.get("timing")
            clean["timing_ambiguous"] = False
            clean["ambiguous_hour"] = None
            clean["ambiguous_timing_candidates"] = None
            clean["invalid_timing_candidates"] = None
            clean["unoffered_type"] = None
            clean["unoffered_value"] = None
            clean["unoffered_timing_mentioned"] = None
            clean["timing_resolution_status"] = "SELECTED"
            clean["timing_selection_attempted"] = True
            clean["customer_intent"] = "TIMING_SELECTION"
        elif final_status == "UNAVAILABLE":
            clean["selected_timing"] = None
            clean["timing_ambiguous"] = False
            clean["ambiguous_hour"] = None
            clean["ambiguous_timing_candidates"] = None
            clean["invalid_timing_candidates"] = final_timing.get("invalid") or None
            clean["unoffered_type"] = "TIMING"
            clean["unoffered_value"] = ", ".join(str(v) for v in (final_timing.get("invalid") or [])) or text.strip()
            clean["unoffered_timing_mentioned"] = clean["unoffered_value"]
            clean["timing_resolution_status"] = "UNAVAILABLE"
            clean["timing_selection_attempted"] = True
            clean["customer_intent"] = "QUESTION"

        return clean
    except Exception as e:
        print(f"[intent_classifier] Extractor error (falling back to safe defaults): {e}")
        flags = _empty_flags()
        try:
            from src.core import kb_loader
            from src.core.helpers import resolve_canonical_teacher
            detected_course = kb_loader.detect_course_from_text_fallback(text, default_course=None)
            if detected_course:
                flags["selected_course"] = detected_course

            # Timing fallback matching
            cid_for_timing = detected_course or active_cid or s.get("course") or "yoga"
            batches_for_fallback = [b.get("time_12h", "").strip() for b in kb_loader.get_available_timings(cid_for_timing)]
            raw_t = text.strip().lower()
            clean_t = raw_t.replace(" ", "").replace("-", "to")
            if clean_t.isdigit():
                t_hr = int(clean_t)
                cands = list(dict.fromkeys([b for b in batches_for_fallback if b.startswith(f"{t_hr}:") or b.startswith(f"0{t_hr}:")]))
                has_am_f = any("am" in c.lower() for c in cands)
                has_pm_f = any("pm" in c.lower() for c in cands)
                if len(cands) == 1 or (not (has_am_f and has_pm_f) and cands):
                    flags["selected_timing"] = cands[0]
                    flags["timing_ambiguous"] = False
                    flags["customer_intent"] = "TIMING_SELECTION"
                elif len(cands) > 1 and has_am_f and has_pm_f:
                    p_hint = (s.get("timing_period") or "").lower()
                    if p_hint in ("evening", "afternoon", "pm"):
                        pm_c = [c for c in cands if "pm" in c.lower()]
                        flags["selected_timing"] = pm_c[0] if pm_c else cands[0]
                        flags["timing_ambiguous"] = False
                    elif p_hint in ("morning", "am"):
                        am_c = [c for c in cands if "am" in c.lower()]
                        flags["selected_timing"] = am_c[0] if am_c else cands[0]
                        flags["timing_ambiguous"] = False
                    else:
                        flags["timing_ambiguous"] = True
                        flags["ambiguous_hour"] = t_hr
                        flags["ambiguous_timing_candidates"] = cands
                        flags["selected_timing"] = None
                    flags["customer_intent"] = "TIMING_SELECTION"
            elif any(w in clean_t for w in ["am", "pm", "to"]):
                for b in batches_for_fallback:
                    b_clean = b.lower().replace(" ", "").replace(":00", "")
                    if clean_t in b_clean or clean_t == b_clean:
                        flags["selected_timing"] = b
                        flags["customer_intent"] = "TIMING_SELECTION"
                        break

            # Package fallback matching
            from src.core.coupons import normalize_package_name
            norm_p = normalize_package_name(text, course_id=cid_for_timing)
            if norm_p:
                flags["selected_package"] = norm_p
                flags["customer_intent"] = "PACKAGE_SELECTION"

            detected_teacher = resolve_canonical_teacher(text, course_id=detected_course)
            if detected_teacher:
                flags["selected_teacher"] = detected_teacher
                flags["customer_intent"] = "TEACHER_SELECTION"
            from src.core.helpers import normalize_day_string
            detected_day = normalize_day_string(text)
            if detected_day:
                flags["selected_days"] = detected_day
                if flags.get("customer_intent") in ("QUESTION", "OTHER"):
                    flags["customer_intent"] = "DAY_SELECTION"
            if kb_loader.is_app_link_request_fallback(text):
                flags["customer_intent"] = "APP_LINK_REQUEST"
            if re.search(r"\b(app guide|app video|download video|how to download|app kaise download|how to install)\b", text, re.IGNORECASE):
                flags["wants_app_guide"] = True
                flags["wants_app_video_guide"] = True
            if re.search(r"\b(trial video|trial guide|how to book trial|trial booking video)\b", text, re.IGNORECASE):
                flags["wants_trial_guide"] = True
                flags["wants_trial_video_guide"] = True
            if re.search(r"\b(qr|qr code|scanner|scan|upi id|payment link|pay link|send link|bhejo qr|scanner bhejo|pay online|payment qr|how to pay|where to pay)\b", text, re.IGNORECASE):
                flags["customer_intent"] = "PAYMENT_REQUEST"
            if re.search(r"\b(paid|already paid|payment done|pay kar diya|payment ho gaya|transfer kar diya|paise bhej diye|fees de di)\b", text, re.IGNORECASE):
                flags["customer_intent"] = "PAYMENT_CLAIM"
                flags["claims_payment_or_enrollment"] = True
            if re.search(r"\b(show\s*n[hi]h?[i]?\s*ho|n[hi]h?[i]?\s*dikh|not\s*showing|classes?\s*(?:n[hi]h?[i]?|show)|batch\s*(?:n[hi]h?[i]?|show)|cannot\s*see|not\s*able\s*to\s*access|class\s*kahan|kaha\s*se\s*join)\b", text, re.IGNORECASE):
                flags["customer_intent"] = "CLASSES_NOT_SHOWING"
                flags["cannot_access_classes"] = True
            if any(w in text.lower() for w in ["demo", "sample", "preview"]):
                flags["wants_demo"] = True
        except Exception:
            pass
        return flags


def is_app_link_request(text: str) -> bool:
    """Backward compatibility helper for app link check."""
    t = (text or "").lower()
    return any(w in t for w in ["app link", "download link", "play store", "app store", "send link", "link bhejo", "link do"])


def apply_extraction_to_state(state: Dict[str, Any], flags: Dict[str, Any]) -> Dict[str, Any]:
    import re
    """
    Merges classifier flags into persistent session state cleanly and monotonically.
    Guarantees:
    1. Zero regression: previously confirmed slots (timing, teacher, package, fee)
       are NEVER overwritten by None, empty strings, questions, disinterest, or casual turns.
    2. Course switching: switching course resets course-specific slots cleanly only
       when the course truly changes.
    3. Deterministic pricing: when package duration is selected, fee is calculated
       via pricing_engine with exact GST and total payable (zero LLM math).
    4. Monotonic stage progression: stage only advances forward via advance_stage().
    """
    from src.state.chat_state import advance_stage
    from src.core import kb_loader
    from src.core.helpers import calculate_exact_fee, resolve_canonical_teacher

    new_state = dict(state)
    intent = flags.get("customer_intent") or "QUESTION"
    is_selection_turn = intent not in ("QUESTION", "OTHER", "DISINTEREST", "COMPLAINT", "GREETING", "AGENT_REQUEST", "PHONE_CALL_REQUEST")

    # RULE 2 (POST-PAYMENT IMMUTABILITY):
    # Pre-Payment (payment_status != 'PAID' and stage != 'ENROLLED'): Unlimited modifications allowed.
    # Post-Payment (payment_status == 'PAID' or stage == 'ENROLLED'): Zero course modification via chat.
    # Course, timing, teacher, days, and package slots are strictly immutable in the database session.
    is_paid = bool(
        new_state.get("payment_status") == "PAID"
        or new_state.get("stage") == "ENROLLED"
        or new_state.get("is_enrolled")
    )
    if is_paid:
        cand_course = flags.get("selected_course")
        is_switching_course = False
        if cand_course:
            entry = kb_loader.get_catalog_entry(cand_course)
            cid_cand = entry["id"] if entry else (kb_loader.detect_course_from_text(cand_course) or None)
            if cid_cand and cid_cand != new_state.get("course"):
                is_switching_course = True

        if not is_switching_course:
            flags = dict(flags)
            flags["selected_course"] = None
            flags["selected_timing"] = None
            flags["unoffered_timing_mentioned"] = None
            flags["selected_teacher"] = None
            flags["selected_days"] = None
            flags["selected_package"] = None

        if flags.get("cannot_access_classes") or flags.get("customer_intent") == "CLASSES_NOT_SHOWING":
            new_state["classes_not_showing"] = True
            if not new_state.get("student_email"):
                new_state["awaiting_email_fulfillment"] = True

    # 1. Course Selection / Switching
    if flags.get("selected_course"):
        raw_c = flags["selected_course"]
        entry = kb_loader.get_catalog_entry(raw_c)
        cand_course = entry["id"] if entry else (kb_loader.detect_course_from_text(raw_c) or None)
        if cand_course:
            old_course = new_state.get("course")
            if old_course and old_course != cand_course:
                # User switched course! Reset course-specific slots unless new ones arrived in the same turn
                if not flags.get("selected_timing"):
                    new_state["timing"] = None
                    new_state["selected_timing"] = None
                    new_state["timing_period"] = None
                    new_state["stage"] = "NEW"
                new_state["auto_assigned_slot_note"] = None
                new_state["teacher_timing_conflict"] = None
                if not flags.get("selected_teacher"):
                    new_state["selected_teacher"] = None
                    new_state["teacher"] = None
                if not flags.get("selected_days"):
                    new_state["days"] = None
                    new_state["selected_days"] = None
                    new_state["days_code"] = None
                if not flags.get("selected_package"):
                    new_state["package"] = None
                    new_state["selected_duration"] = None
                    new_state["fee"] = None
                    new_state["base_fee"] = None
                    new_state["offer_fee"] = None
                    new_state["gst_amount"] = None
                    new_state["gst_percentage"] = None
            new_state["course"] = cand_course

    active_course = new_state.get("course")
    if flags.get("timing_resolution_status") != "NONE":
        new_state["timing_resolution_status"] = flags.get("timing_resolution_status")

    # 2. Timing Slot Merge (Only on selection turns or when explicitly provided)
    if flags.get("timing_selection_attempted") and flags.get("timing_resolution_status") in {
        "UNAVAILABLE", "AMBIGUOUS", "PERIOD_ONLY", "UNRESOLVED", "MULTIPLE_VALID", "MIXED"
    } and not is_paid:
        # A new timing attempt supersedes stale pre-payment timing state. Keep
        # the customer at the timing step; never let the old slot drive teacher
        # or package progression after a correction.
        new_state["timing"] = None
        new_state["selected_timing"] = None
        new_state["timing_ambiguous"] = flags.get("timing_ambiguous", False)
        new_state["ambiguous_hour"] = flags.get("ambiguous_hour")
        new_state["ambiguous_timing_candidates"] = flags.get("ambiguous_timing_candidates")
        new_state["selected_teacher"] = None
        new_state["teacher"] = None
        new_state["package"] = None
        new_state["selected_duration"] = None
        new_state["fee"] = None
        new_state["base_fee"] = None
        new_state["offer_fee"] = None
        new_state["gst_amount"] = None
        new_state["gst_percentage"] = None
        new_state["timing_resolution_status"] = flags.get("timing_resolution_status")
        new_state["stage"] = "ENROLL_ASKED"
    elif flags.get("timing_ambiguous"):
        new_state["timing_ambiguous"] = True
        new_state["ambiguous_hour"] = flags.get("ambiguous_hour")
        new_state["ambiguous_timing_candidates"] = flags.get("ambiguous_timing_candidates")
        new_state["timing"] = None
        new_state["selected_timing"] = None
    elif flags.get("selected_timing"):
        new_state["timing_ambiguous"] = False
        new_state["ambiguous_hour"] = None
        new_state["ambiguous_timing_candidates"] = None
    if flags.get("timing_period"):
        new_state["timing_period"] = flags["timing_period"]
        # If teacher is already known, evaluate dynamic constraints immediately
        curr_teacher = new_state.get("selected_teacher") or new_state.get("teacher")
        if active_course and curr_teacher and not new_state.get("timing") and not is_paid:
            from src.core.helpers.schedule_engine import resolve_teacher_timing_constraints
            c_res = resolve_teacher_timing_constraints(
                active_course, teacher=curr_teacher, period=flags["timing_period"]
            )
            if c_res.get("auto_assigned_timing"):
                new_state["timing"] = c_res["auto_assigned_timing"]
                new_state["selected_timing"] = c_res["auto_assigned_timing"]
                new_state["auto_assigned_slot_note"] = c_res.get("constraint_note")
                new_state["stage"] = advance_stage(new_state.get("stage", "NEW"), "TIMING_SELECTED")

    if flags.get("selected_timing") and is_selection_turn:
        old_timing = new_state.get("timing") or new_state.get("selected_timing")
        new_timing = flags["selected_timing"]
        new_state["timing"] = new_timing
        new_state["selected_timing"] = new_timing

        # Check teacher compatibility against the newly selected timing
        curr_teacher = new_state.get("selected_teacher") or new_state.get("teacher")
        if curr_teacher and active_course:
            from src.core.helpers.schedule_engine import get_slots_for_teacher, extract_time_tokens
            t_slots = get_slots_for_teacher(active_course, curr_teacher)
            c_hr, c_merid = extract_time_tokens(new_timing)
            teaches_new = any(
                extract_time_tokens(s.get("time_12h")) == (c_hr, c_merid)
                for s in t_slots
                if c_hr is not None
            )
            if not teaches_new:
                from src.core.helpers.schedule_engine import get_teachers_for_slot
                timing_teachers = get_teachers_for_slot(active_course, new_timing)
                if timing_teachers:
                    new_state["teacher_timing_conflict"] = {
                        "requested_timing": new_timing,
                        "current_teacher": curr_teacher,
                        "timing_teachers": timing_teachers,
                    }
                    # Keep selected_teacher, do NOT silently switch without asking the customer
                    new_state["timing"] = None
                    new_state["selected_timing"] = None
                else:
                    new_state["selected_teacher"] = None
                    new_state["teacher"] = None
                    new_state["days"] = None
                    new_state["selected_days"] = None
                    new_state["days_code"] = None
                    new_state["stage"] = "TIMING_SELECTED"
        else:
            new_state["stage"] = advance_stage(new_state.get("stage", "NEW"), "TIMING_SELECTED")
    elif flags.get("unoffered_timing_mentioned") or flags.get("unoffered_type") == "TIMING":
        new_state["timing"] = None
        new_state["selected_timing"] = None

    # 3. Teacher Slot Merge (On selection turns OR when teacher is explicitly mentioned / demo requested)
    teacher_flag = flags.get("selected_teacher")
    timing_unresolved = flags.get("timing_selection_attempted") and flags.get("timing_resolution_status") in {
        "UNAVAILABLE", "AMBIGUOUS", "PERIOD_ONLY", "UNRESOLVED", "MULTIPLE_VALID", "MIXED"
    }
    if teacher_flag and not timing_unresolved and (
        is_selection_turn or flags.get("wants_demo") or not new_state.get("selected_teacher")
    ):
        resolved = resolve_canonical_teacher(teacher_flag, course_id=active_course)
        teacher_val = resolved or teacher_flag
        new_state["selected_teacher"] = teacher_val
        new_state["teacher"] = teacher_val
        new_state["stage"] = advance_stage(new_state.get("stage", "NEW"), "TEACHER_SELECTED")

        # Deterministic Auto-Binding of Timing & Reverse Conflict Check
        if active_course and teacher_val and not is_paid:
            current_timing = new_state.get("timing") or new_state.get("selected_timing")
            from src.core.helpers.schedule_engine import resolve_teacher_timing_constraints
            c_res = resolve_teacher_timing_constraints(
                active_course, teacher=teacher_val, timing=current_timing, period=new_state.get("timing_period")
            )
            if c_res.get("is_conflict"):
                # New teacher does not teach current timing! Invalidate timing and dependent slots
                new_state["timing"] = None
                new_state["selected_timing"] = None
                new_state["days"] = None
                new_state["selected_days"] = None
                new_state["days_code"] = None
                new_state["auto_assigned_slot_note"] = c_res.get("constraint_note")
            elif c_res.get("auto_assigned_timing") and not current_timing:
                new_state["timing"] = c_res["auto_assigned_timing"]
                new_state["selected_timing"] = c_res["auto_assigned_timing"]
                new_state["auto_assigned_slot_note"] = c_res.get("constraint_note")
                new_state["stage"] = advance_stage(new_state.get("stage", "NEW"), "TIMING_SELECTED")

    # Deterministic Auto-Binding of Single Teacher from Timing:
    active_timing = new_state.get("timing") or new_state.get("selected_timing")
    if active_course and active_timing and not new_state.get("selected_teacher") and not is_paid:
        from src.core.helpers.schedule_engine import resolve_teacher_timing_constraints
        t_res = resolve_teacher_timing_constraints(active_course, timing=active_timing)
        if t_res.get("auto_assigned_teacher"):
            auto_t = t_res["auto_assigned_teacher"]
            new_state["selected_teacher"] = auto_t
            new_state["teacher"] = auto_t
            new_state["auto_assigned_slot_note"] = t_res.get("constraint_note")
            new_state["stage"] = advance_stage(new_state.get("stage", "NEW"), "TEACHER_SELECTED")

    # 3.5 Days Slot Merge & Deterministic Auto-Binding
    # A. Explicit day selection from customer
    if flags.get("selected_days"):
        from src.core.helpers.schedule_engine import normalize_day_string, get_day_code
        norm_d = normalize_day_string(flags["selected_days"])
        if norm_d:
            new_state["days"] = norm_d
            new_state["selected_days"] = norm_d
            new_state["days_code"] = get_day_code(norm_d)
            new_state["stage"] = advance_stage(new_state.get("stage", "NEW"), "DAYS_SELECTED")

    # B. Dynamic Days Auto-Binding vs Confirmation
    # Whenever course is known, check if timing/teacher restricts days to exactly 1 option
    active_timing = new_state.get("timing") or new_state.get("selected_timing")
    active_teacher = new_state.get("selected_teacher") or new_state.get("teacher")

    if active_course and not is_paid:
        from src.core.helpers.schedule_engine import (
            get_available_days_for_batch,
            get_day_code,
            normalize_day_string,
        )
        avail_days = get_available_days_for_batch(
            course_id=active_course,
            timing=active_timing,
            teacher=active_teacher,
        )

        raw_user_text = (flags.get("_raw_text") or "").strip()
        user_stated_day = flags.get("selected_days") or normalize_day_string(raw_user_text)

        if len(avail_days) == 1:
            # AUTO-BIND: Exactly 1 day option exists for this teacher + timing!
            # Automatically bind without asking the user.
            auto_day = avail_days[0]
            new_state["days"] = auto_day
            new_state["selected_days"] = auto_day
            new_state["days_code"] = get_day_code(auto_day)
        elif len(avail_days) > 1:
            # Multiple day options exist for this teacher + timing!
            if user_stated_day and user_stated_day in avail_days:
                # User specified/confirmed one of the valid day options
                new_state["days"] = user_stated_day
                new_state["selected_days"] = user_stated_day
                new_state["days_code"] = get_day_code(user_stated_day)
            elif new_state.get("days") and new_state.get("days") in avail_days:
                # Previously selected day is still valid for this combination
                new_state["selected_days"] = new_state["days"]
                new_state["days_code"] = get_day_code(new_state["days"])
            else:
                # Days ambiguous! Keep None so conversation asks user to confirm
                new_state["days"] = None
                new_state["selected_days"] = None
                new_state["days_code"] = None

    # 4. Package / Duration Slot Merge & Deterministic Pricing
    if flags.get("unoffered_type") == "PACKAGE":
        new_state["package"] = None
        new_state["selected_duration"] = None
        new_state["fee"] = None
        new_state["base_fee"] = None
        new_state["offer_fee"] = None
        new_state["gst_amount"] = None
        new_state["gst_percentage"] = None
    elif flags.get("selected_package") and (is_selection_turn or flags.get("customer_intent") == "PACKAGE_SELECTION" or not bool(re.search(r"(\?|kya|kitn[aei]|how much|cost|fees?)", flags.get("_raw_text", ""), re.IGNORECASE))):
        from src.core.coupons import normalize_package_name
        canonical_pkg = normalize_package_name(flags["selected_package"], course_id=active_course or "yoga")
        pkg_val = canonical_pkg or flags["selected_package"]
        new_state["package"] = pkg_val
        new_state["selected_duration"] = pkg_val
        new_state["stage"] = advance_stage(new_state.get("stage", "NEW"), "PACKAGE_SELECTED")

        # Deterministic fee calculation via pricing engine
        if active_course:
            pricing = calculate_exact_fee(active_course, pkg_val)
            if pricing and "total_payable" in pricing:
                new_state["fee"] = pricing.get("total_payable")
                new_state["base_fee"] = pricing.get("base_price")
                new_state["offer_fee"] = pricing.get("offer_price")
                new_state["gst_amount"] = pricing.get("gst_amount")
                new_state["gst_percentage"] = pricing.get("gst_percentage")

    # 5. App & Profile State Merge
    app_stated = flags.get("app_installed_stated", "unmentioned")
    if app_stated == "confirmed":
        new_state["app_installed"] = True
    elif app_stated == "denied":
        new_state["app_installed"] = False

    profile_stated = flags.get("profile_created_stated", "unmentioned")
    if profile_stated == "confirmed":
        new_state["profile_created"] = True
        new_state["app_installed"] = True
        new_state["stage"] = advance_stage(new_state.get("stage", "NEW"), "PROFILE_COMPLETED")
    elif profile_stated == "denied":
        new_state["profile_created"] = False

    if flags.get("wants_trial"):
        new_state["wants_trial"] = True

    if flags.get("claims_payment_or_enrollment"):
        new_state["claims_payment"] = True

    # Language management: Lock selected language until explicitly changed
    from src.core import language_loader

    raw_text = (flags.get("_raw_text") or "").strip()
    explicit_req = flags.get("explicit_language_request")
    matched_explicit = language_loader.match_language(explicit_req) if explicit_req else None
    if not matched_explicit and explicit_req:
        matched_explicit = explicit_req.strip().title()

    # Check if raw text directly matches a language name or button
    if not matched_explicit and raw_text:
        # Check direct language button / option clicks
        matched_from_text = language_loader.match_language(raw_text)
        if matched_from_text:
            matched_explicit = matched_from_text
        else:
            # Check explicit switch phrases like "talk in Hindi", "English please"
            import re
            m = re.search(r"\b(?:speak|talk|reply|switch|change|baat)\s+(?:in|to|me|mein)?\s*([a-zA-Z]+)", raw_text, re.IGNORECASE)
            if m:
                matched_explicit = language_loader.match_language(m.group(1))

    # [PREVIOUS BEHAVIOR - COMMENTED OUT]:
    # if matched_explicit:
    #     new_state["language"] = matched_explicit
    #     new_state["language_selected"] = True
    # elif not new_state.get("language"):
    #     # Initial fallback if language not yet selected
    #     detected_lang = flags.get("detected_language") or language_loader.get_default_language()
    #     _NATIVE_SCRIPT_MARKERS = (
    #         "devanagari", "telugu", "tamil", "bengali", "gujarati",
    #         "malayalam", "kannada", "odia", "punjabi", "arabic",
    #         "urdu", "thai", "myanmar", "sinhala",
    #     )
    #     if raw_text and all(ord(c) < 256 for c in raw_text):
    #         if detected_lang and any(m in detected_lang.lower() for m in _NATIVE_SCRIPT_MARKERS):
    #             detected_lang = "Hinglish (Latin script)"
    #     new_state["language"] = detected_lang

    _NATIVE_SCRIPT_MARKERS = (
        "devanagari", "telugu", "tamil", "bengali", "gujarati",
        "malayalam", "kannada", "odia", "punjabi", "arabic",
        "urdu", "thai", "myanmar", "sinhala",
    )

    detected_lang = flags.get("detected_language")
    has_native_script = bool(raw_text and any(ord(c) >= 256 for c in raw_text))

    if matched_explicit:
        new_state["language"] = matched_explicit
        new_state["language_selected"] = True
    elif has_native_script and detected_lang:
        # User typed in a native script (e.g. Malayalam, Tamil, Devanagari, Telugu, etc.)
        new_state["language"] = detected_lang
        new_state["language_selected"] = True
    elif not new_state.get("language_selected"):
        # Language was not explicitly locked; dynamically adapt to detected language
        detected_lang = detected_lang or new_state.get("language") or language_loader.get_default_language()
        if raw_text and all(ord(c) < 256 for c in raw_text) and detected_lang:
            d_lower = detected_lang.lower()
            if "devanagari" in d_lower:
                detected_lang = "Hinglish (Latin script)"
            elif any(m in d_lower for m in _NATIVE_SCRIPT_MARKERS) and "latin" not in d_lower:
                base_lang = detected_lang.split()[0].title()
                detected_lang = f"{base_lang} (Latin script)"
        new_state["language"] = detected_lang
    elif detected_lang and "english" not in detected_lang.lower() and new_state.get("language") == "English":
        # Dynamic switch: User had default English, but started speaking a regional language
        if raw_text and all(ord(c) < 256 for c in raw_text):
            d_lower = detected_lang.lower()
            if "devanagari" in d_lower:
                detected_lang = "Hinglish (Latin script)"
            elif any(m in d_lower for m in _NATIVE_SCRIPT_MARKERS) and "latin" not in d_lower:
                base_lang = detected_lang.split()[0].title()
                detected_lang = f"{base_lang} (Latin script)"
        new_state["language"] = detected_lang

    if flags.get("conversation_summary"):
        new_state["conversation_summary"] = flags["conversation_summary"]

    new_state["_last_turn"] = {
        "intent": flags.get("customer_intent"),
        "timing_resolution_status": flags.get("timing_resolution_status", "NONE"),
        "timing_ambiguous": flags.get("timing_ambiguous", False),
        "ambiguous_hour": flags.get("ambiguous_hour"),
        "ambiguous_timing_candidates": flags.get("ambiguous_timing_candidates"),
        "invalid_timing_candidates": flags.get("invalid_timing_candidates"),
        "wants_trial": flags.get("wants_trial", False),
        "wants_demo": flags.get("wants_demo", False),
        "wants_app_guide": flags.get("wants_app_guide", False),
        "wants_app_video_guide": flags.get("wants_app_guide", False),
        "wants_trial_guide": flags.get("wants_trial_guide", False),
        "wants_trial_video_guide": flags.get("wants_trial_guide", False),
        "wants_resend_video": flags.get("wants_resend_video", False),
        "requests_human_agent": flags.get("requests_human_agent", False),
        "is_coupon_requested": flags.get("is_coupon_requested", False),
        "is_disinterested": flags.get("is_disinterested", False),
        "is_complaint_or_refund": flags.get("is_complaint_or_refund", False),
        "is_out_of_scope": flags.get("is_out_of_scope", False),
        "question_summary": flags.get("question_summary"),
        "detected_language": flags.get("detected_language"),
        "claims_payment_or_enrollment": flags.get("claims_payment_or_enrollment", False),
        "cannot_access_classes": flags.get("cannot_access_classes", False),
        "unoffered_type": flags.get("unoffered_type"),
        "unoffered_value": flags.get("unoffered_value"),
        "unoffered_timing_mentioned": flags.get("unoffered_timing_mentioned") or (flags.get("unoffered_value") if flags.get("unoffered_type") == "TIMING" else None),
        "conversation_summary": flags.get("conversation_summary"),
    }

    return new_state
