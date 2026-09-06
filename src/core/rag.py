"""
rag.py — RAG (Retrieval-Augmented Generation) pipeline.
Retrieves relevant knowledge chunks from Qdrant vector DB and generates
AI responses using OpenAI LLM with session state context.
"""

import os
import time
import asyncio
from dotenv import load_dotenv

# IMPORTANT: load_dotenv MUST be called before any client initialization
load_dotenv()

# from langchain_google_genai import ChatGoogleGenerativeAI
from qdrant_client import QdrantClient
from langchain_qdrant import QdrantVectorStore
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from src.core.prompt import format_system_prompt
import json

# --- Qdrant Vector DB Connection ---
retriever = None
try:
    client = QdrantClient(
        url=os.getenv("QDRANT_URL"),
        api_key=os.getenv("QDRANT_API_KEY"),
        timeout=10,
        check_compatibility=False,
    )


    embeddings = OpenAIEmbeddings(
        model="text-embedding-3-large",
        api_key=os.getenv("OPENAI_API_KEY")
    )

    db = QdrantVectorStore(
        client=client,
        collection_name="knowledge_base",
        embedding=embeddings,
    )

    retriever = db.as_retriever(
        search_kwargs={"k": 8}
    )
    print("[rag.py] Qdrant vector DB connected successfully")
except Exception as e:
    print(f"[rag.py] Could not connect to Qdrant, RAG will be disabled: {e}")

# --- LLM Model ---
llm = ChatOpenAI(
    model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
    api_key=os.getenv("OPENAI_API_KEY"),
    temperature=0.3,
    timeout=30,
    max_retries=2,
)
print(f"[rag.py] LLM initialized: gpt-4o-mini")

from src.core import kb_loader

# Backward-compatibility default module exports (derived dynamically from kb_loader)
VALID_TIMINGS = kb_loader.get_valid_timings("yoga")
VALID_PACKAGES = kb_loader.get_valid_packages("yoga")

async def extract_slot_llm(text: str, chat_history: list = None, course_id: str = "yoga") -> dict:
    """
    Determines if the user's message clearly selects a batch timing and/or
    package duration for the active course. Returns {"timing": str|None, "package": str|None}.
    Never guesses — returns None for anything not unambiguously stated.
    """
    valid_timings = kb_loader.get_valid_timings(course_id)
    valid_packages = kb_loader.get_valid_packages(course_id)

    sys_msg = SystemMessage(content=(
        f"You extract structured slot values from a WhatsApp {course_id}-enrollment message.\n"
        f"Valid timings: {valid_timings}\n"
        f"Valid packages: {list(valid_packages.keys())}\n"
        "If the message CLEARLY and UNAMBIGUOUSLY selects ONE of these timings and/or "
        "ONE of these packages (any language, phrasing, or typo), return the exact matching "
        "string from the lists above. If ambiguous or not selected, return null for that field. "
        "NEVER guess a value the user did not clearly indicate.\n"
        'Respond ONLY with strict JSON: {"timing": "<value or null>", "package": "<value or null>"}'
    ))
    
    last_ai_message = ""
    if chat_history:
        for turn in reversed(chat_history):
            if turn.get("role") == "assistant":
                last_ai_message = turn.get("content", "")
                break

    user_msg = (
        f"AI's Last Message: \"{last_ai_message}\"\n"
        f"Customer's Current Reply: \"{text}\"\n\n"
        "Extract the slot values from the customer's reply, interpreting it strictly in the context of what the AI just asked."
    )

    try:
        resp = await llm.ainvoke([sys_msg, HumanMessage(content=user_msg)])
        raw = resp.content.strip().strip("`").replace("json\n", "").strip()
        data = json.loads(raw)
        timing = data.get("timing")
        package = data.get("package")
        return {
            "timing": timing if timing in valid_timings else None,
            "package": package if package in valid_packages else None,
        }
    except Exception as e:
        print(f"[extract_slot_llm] error: {e}")
        return {"timing": None, "package": None}

def _build_history_messages(chat_history: list = None):
    """
    Converts stored history (list of {"role": ..., "content": ...}
    dicts, oldest first) into LangChain message objects the model can
    read as prior conversation turns.
    """
    if not chat_history:
        return []

    messages = []
    # Cap history to recent 10 turns for sharp focus
    recent_turns = chat_history[-10:] if len(chat_history) > 10 else chat_history
    for turn in recent_turns:
        if turn["role"] == "user":
            messages.append(HumanMessage(content=turn["content"]))
        elif turn["role"] == "assistant":
            messages.append(AIMessage(content=turn["content"]))
    return messages



def _build_retrieval_query(question: str, chat_history: list = None) -> str:
    """
    Universal clean vector retrieval query builder.
    Preserves the customer's exact question words so Qdrant vector embeddings match
    the user's specific intent with 100% precision.
    Never overwrites or replaces user queries with hardcoded static strings.
    """
    q_clean = question.strip()
    if not q_clean:
        return question

    q_lower = q_clean.lower()
    words = q_lower.split()

    # Handle ambiguous short follow-up pronouns (e.g., "unka fee kitna hai")
    followup_pronouns = ["unka", "unki", "unke", "inka", "inki", "inke", "she", "he", "her", "his", "they", "them", "woh", "wo", "uska", "uski", "uske"]
    is_ambiguous_short = len(words) <= 4 and any(p in words for p in followup_pronouns)

    if is_ambiguous_short and chat_history:
        for turn in reversed(chat_history):
            content = turn.get("content", "").strip()
            if content:
                return f"{content[:50]} {q_clean}"

    # For long noisy multiline/burst inputs (> 8 words), filter out conversational stop words
    # while preserving ALL meaningful user topic words intact (teacher names, locations, qualifications, fees)
    if len(words) > 8:
        fillers = {"mujhe", "yhaa", "pr", "koi", "bhii", "and", "bhi", "se", "ka", "ki", "ke", "hai", "hu", "mera", "meri", "chahiye", "karne", "karna", "krna", "batao", "sir", "mam", "ma'am", "ji"}
        clean_words = [w for w in words if w not in fillers]
        if len(clean_words) >= 2:
            return " ".join(clean_words)

    return q_clean


async def ask_rag_async(question: str, chat_history: list = None, state: dict = None) -> dict:
    """
    Async RAG query function. Retrieves relevant knowledge chunks from Qdrant
    and passes them alongside session state to OpenAI LLM.
    Returns dict: {"reply": str, "sources": str} where sources are the Qdrant chunks retrieved.
    """
    t0 = time.perf_counter()
    try:
        # Check if user explicitly mentioned a course from catalog to switch context
        detected = kb_loader.detect_course_from_text(question, default_course=None)
        if detected:
            course_id = detected
            if state is not None:
                state["course"] = detected
        else:
            course_id = (state or {}).get("course") or "yoga"

        sys_prompt_content = format_system_prompt(state or {"stage": "NEW"}, course_id=course_id)
        context = ""
        sources_text = ""       # Qdrant source chunk previews for CSV
        retrieval_query_used = ""  # Exact query sent to Qdrant

        # Run vector database search if retriever is loaded
        if retriever is not None:
            retrieval_query_used = _build_retrieval_query(question, chat_history)
            t_retrieve = time.perf_counter()
            try:
                docs = await retriever.ainvoke(retrieval_query_used)
            except Exception:
                docs = await asyncio.to_thread(retriever.invoke, retrieval_query_used)
            print(f"[TIMING] rag retrieval: {time.perf_counter() - t_retrieve:.2f}s ({len(docs)} docs)")
            print(f"[PROMPT-DEBUG] RETRIEVAL_QUERY: {retrieval_query_used!r}")
            context = "\n\n".join([doc.page_content for doc in docs])
            sources_text = " | ".join([doc.page_content[:100].replace("\n", " ") for doc in docs]) if docs else ""
        else:
            print(f"[TIMING] rag retrieval: SKIPPED (transactional input)")
            docs = []

        # Construct message payload for LangChain OpenAI LLM
        messages = [SystemMessage(content=sys_prompt_content)]
        messages.extend(_build_history_messages(chat_history))

        # Extract the last AI message for immediate context pairing
        last_ai_message = ""
        if chat_history:
            for turn in reversed(chat_history):
                if turn.get("role") == "assistant":
                    last_ai_message = turn.get("content", "")
                    break

        user_msg = (
            f"Context:\n{context}\n\n"
            f"--- IMMEDIATE CONTEXT ---\n"
            f"AI's Last Message: \"{last_ai_message}\"\n"
            f"Customer's Current Reply: \"{question}\"\n\n"
            "INSTRUCTION:\n"
            "1. Analyze the customer's reply specifically as an answer to the AI's last message.\n"
            "2. Read the customer's message carefully. It may contain multiple distinct questions or fragments of questions combined.\n"
            "3. Identify and answer EVERY distinct question or topic raised in the input. Do not skip any question.\n"
            "4. Reconstruct whether the input is one continued question, multiple separate ones, or a mix — then answer each reconstructed question fully.\n"
            "5. STRICT LANGUAGE MATCHING: Match the language of 'Customer's Current Reply'. If English, reply ONLY in pure English (0% Hindi/Hinglish). If Hinglish, reply in Hinglish. If Hindi, reply in Hindi."
        )

        messages.append(HumanMessage(content=user_msg))

        # ── Issue 4: Transient LLM Rate Limit Retry Handler with Exponential Backoff ──
        # Seamlessly retries up to 3 times on transient rate limits (429), timeouts, or network spikes
        max_llm_retries = 3
        response = None
        last_llm_err = None
        for attempt in range(1, max_llm_retries + 1):
            try:
                t_llm = time.perf_counter()
                response = await llm.ainvoke(messages)
                print(f"[TIMING] rag LLM call (attempt {attempt}): {time.perf_counter() - t_llm:.2f}s")
                break
            except Exception as llm_err:
                last_llm_err = llm_err
                err_str = str(llm_err).lower()
                is_transient = any(code in err_str for code in ["429", "rate_limit", "timeout", "timed out", "connection", "overloaded", "server error"])
                if attempt < max_llm_retries and is_transient:
                    backoff = 1.0 * (2 ** (attempt - 1))  # 1.0s, 2.0s exponential backoff
                    print(f"[rag.py] LLM transient error on attempt {attempt}/{max_llm_retries} ({llm_err}). Retrying in {backoff}s...")
                    await asyncio.sleep(backoff)
                else:
                    # Fallback attempt via synchronous thread invocation before giving up
                    try:
                        t_llm = time.perf_counter()
                        response = await asyncio.to_thread(llm.invoke, messages)
                        print(f"[TIMING] rag LLM sync fallback call: {time.perf_counter() - t_llm:.2f}s")
                        break
                    except Exception as final_err:
                        last_llm_err = final_err
                        if attempt >= max_llm_retries:
                            raise final_err

        result = response.content.strip()
        print(f"[TIMING] rag TOTAL: {time.perf_counter() - t0:.2f}s")
        return {"reply": result, "sources": sources_text, "retrieval_query": retrieval_query_used}


    except Exception as e:
        error = str(e).lower()
        if "quota" in error or "429" in error or "resource_exhausted" in error:
            return {"reply": "Sorry, the AI service has reached its usage limit. Please try again later.", "sources": "", "retrieval_query": ""}
        print(f"[rag.py] ask_rag_async error: {e}")
        return {"reply": "Sorry, I'm unable to process your request right now.", "sources": "", "retrieval_query": ""}


def stream_rag(question: str, chat_history: list = None, state: dict = None):
    """
    Synchronous RAG fallback — yields the complete AI reply as a single chunk.
    Used only as a fallback when async is not available.
    """
    try:
        # Check if user explicitly mentioned a course from catalog to switch context
        detected = kb_loader.detect_course_from_text(question, default_course=None)
        if detected:
            course_id = detected
            if state is not None:
                state["course"] = detected
        else:
            course_id = (state or {}).get("course") or "yoga"

        sys_prompt_content = format_system_prompt(state or {"stage": "NEW"}, course_id=course_id)
        context = ""

        if retriever is not None:
            retrieval_query = _build_retrieval_query(question, chat_history)
            docs = retriever.invoke(retrieval_query)
            context = "\n\n".join([doc.page_content for doc in docs])

        messages = [SystemMessage(content=sys_prompt_content)]
        messages.extend(_build_history_messages(chat_history))

        last_ai_message = ""
        if chat_history:
            for turn in reversed(chat_history):
                if turn.get("role") == "assistant":
                    last_ai_message = turn.get("content", "")
                    break

        user_msg = (
            f"Context:\n{context}\n\n"
            f"--- IMMEDIATE CONTEXT ---\n"
            f"AI's Last Message: \"{last_ai_message}\"\n"
            f"Customer's Current Reply: \"{question}\"\n\n"
            "INSTRUCTION:\n"
            "1. Analyze the customer's reply specifically as an answer to the AI's last message.\n"
            "2. Read the customer's message carefully. It may contain multiple distinct questions or fragments of questions combined.\n"
            "3. Identify and answer EVERY distinct question or topic raised in the input. Do not skip any question.\n"
            "4. Reconstruct whether the input is one continued question, multiple separate ones, or a mix — then answer each reconstructed question fully.\n"
            "5. Follow all constraints in the system prompt (e.g. no follow-up questions, respond in the correct language, answer only what is asked)."
        )
        messages.append(HumanMessage(content=user_msg))

        response = llm.invoke(messages)
        answer = response.content.strip()

        if answer:
            yield answer

    except Exception as e:
        error = str(e).lower()
        if "quota" in error or "429" in error or "resource_exhausted" in error:
            yield "Sorry, the AI service has reached its daily usage limit. Please try again later."
            return
        print(f"[rag.py] stream_rag error: {e}")
        yield "Sorry, I'm unable to answer your question right now."




