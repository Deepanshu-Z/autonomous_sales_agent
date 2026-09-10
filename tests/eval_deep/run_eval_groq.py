"""
run_eval_groq.py — Deep eval runner using GROQ ONLY (no ChatGPT).

Uses model: openai/gpt-oss-120b via Groq's OpenAI-compatible API.
Saves results to test_result/test_result_GROQ.txt

Includes token limiter: if prompt exceeds ~6,500 tokens (approx 23,000 chars),
it truncates the extra tokens to stay strictly within Groq's 8,000 TPM limit.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional

sys.stdout.reconfigure(encoding="utf-8")
_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, _HERE)

from dotenv import load_dotenv
load_dotenv()

from questions import CONVERSATIONS, INDEPENDENT, empty_state
from src.core import conversation_handler, intent_classifier, refusal_engine
from src.core import prompt as prompt_module
from langchain_openai import ChatOpenAI

DEFAULT_OUT = os.path.join(ROOT, "test_result", "test_result_GROQ.txt")

# Max character limit for system prompt (~6,200 tokens) to ensure prompt + user msg < 8k tokens
MAX_PROMPT_CHARS = 23000


def truncate_prompt_tokens(prompt_text: str, max_chars: int = MAX_PROMPT_CHARS) -> str:
    """If prompt is greater than token limit, slice off the extra tokens."""
    if len(prompt_text) > max_chars:
        # Cut at max_chars, keeping the essential earlier rules and course data intact
        return prompt_text[:max_chars]
    return prompt_text


# Patch prompt_module.format_system_prompt to enforce token cap
_orig_format_system_prompt = prompt_module.format_system_prompt


def _capped_format_system_prompt(*args, **kwargs) -> str:
    raw = _orig_format_system_prompt(*args, **kwargs)
    return truncate_prompt_tokens(raw, MAX_PROMPT_CHARS)


prompt_module.format_system_prompt = _capped_format_system_prompt


def setup_groq_llms(model: str = "openai/gpt-oss-120b"):
    groq_key = os.getenv("GROQ_API_KEY")
    if not groq_key:
        raise ValueError("GROQ_API_KEY is not set in environment or .env file!")

    base_url = "https://api.groq.com/openai/v1"
    print(f"🔧 Initializing Groq LLM: model='{model}' | base_url='{base_url}'")
    print(f"🛡️ Active Token Limiter: Truncating prompts > {MAX_PROMPT_CHARS} chars (~6,200 tokens) to guarantee < 8k tokens.")

    reply_llm = ChatOpenAI(
        model=model,
        api_key=groq_key,
        base_url=base_url,
        temperature=0.2,
        max_retries=2,
        timeout=35,
        model_kwargs={"response_format": {"type": "json_object"}},
    )

    classifier_llm = ChatOpenAI(
        model=model,
        api_key=groq_key,
        base_url=base_url,
        temperature=0,
        max_retries=2,
        timeout=25,
    )

    refusal_llm = ChatOpenAI(
        model=model,
        api_key=groq_key,
        base_url=base_url,
        temperature=0.2,
        max_retries=2,
        timeout=25,
    )

    # Monkey-patch handlers to strictly use GROQ
    conversation_handler._reply_llm = reply_llm
    intent_classifier._classifier_llm = classifier_llm
    refusal_engine._refusal_llm = refusal_llm
    print("✅ Groq LLMs hooked into conversation_handler, intent_classifier, refusal_engine.")


def _format_answer(result: Dict[str, Any]) -> str:
    reply = (result.get("reply") or "").strip()
    followup = (result.get("followup") or "") or ""
    followup = str(followup).strip()
    if followup:
        return f"{reply}\n\n[FOLLOWUP]\n{followup}"
    return reply or "[EMPTY REPLY]"


def _history_append(history: List[Dict[str, str]], user: str, answer: str) -> None:
    history.append({"role": "user", "content": user})
    history.append({"role": "assistant", "content": answer})


async def _ask_with_retry(
    text: str,
    state: Dict[str, Any],
    history: List[Dict[str, str]],
    max_retries: int = 6,
) -> Dict[str, Any]:
    for attempt in range(max_retries):
        try:
            return await conversation_handler.handle_incoming_message(
                user_message=text,
                session_state=state,
                conversation_history=history,
                course_id=state.get("course"),
            )
        except Exception as e:
            err_msg = str(e)
            # Check if rate limited
            if "rate limit" in err_msg.lower() or "429" in err_msg or "413" in err_msg:
                # Look for suggested wait time like "try again in 5.2s"
                match = re.search(r"try again in ([\d\.]+)s", err_msg, re.IGNORECASE)
                if match:
                    wait_time = float(match.group(1)) + 2.0
                else:
                    wait_time = 10.0 * (attempt + 1)
                print(f"       ⚠️ Rate limit/TPM cap hit. Pausing for {wait_time:.1f}s (attempt {attempt+1}/{max_retries})...")
                await asyncio.sleep(wait_time)
            elif attempt < max_retries - 1:
                wait_time = 3.0 * (attempt + 1)
                print(f"       ⚠️ Transient error: {e}. Retrying in {wait_time:.1f}s...")
                await asyncio.sleep(wait_time)
            else:
                raise e


async def run(out_path: str, sleep_s: float, limit: Optional[int], model: str) -> None:
    setup_groq_llms(model)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    n = 0
    errors = 0

    independent = INDEPENDENT if not limit else INDEPENDENT[:limit]
    convos = CONVERSATIONS if not limit else (CONVERSATIONS[: max(1, limit // 4)] if limit > 1 else [])

    with open(out_path, "w", encoding="utf-8") as f:
        pass

    async def one_turn(text: str, state: Dict[str, Any], history: List[Dict[str, str]]) -> str:
        nonlocal n, errors
        n += 1
        qn = n
        print(f"[{qn}] {text[:80]}{'...' if len(text) > 80 else ''}", flush=True)
        t0 = time.perf_counter()
        try:
            result = await _ask_with_retry(text, state, history)
            elapsed = time.perf_counter() - t0
            print(f"    ok {elapsed:.2f}s", flush=True)
            new_state = result.get("new_state") or state
            state.clear()
            state.update(new_state)
            answer = _format_answer(result)
        except Exception as exc:
            errors += 1
            elapsed = time.perf_counter() - t0
            print(f"    FAIL {elapsed:.2f}s {exc}", flush=True)
            answer = f"[EVAL ERROR] {exc}"
        with open(out_path, "a", encoding="utf-8") as f:
            f.write(f"Q{qn}: {text}\nA{qn}: {answer}\n\n")
            f.flush()
        _history_append(history, text, answer)
        if sleep_s:
            await asyncio.sleep(sleep_s)
        return answer

    for item in independent:
        st = empty_state(item.get("initial_state"))
        hist: List[Dict[str, str]] = []
        await one_turn(item["text"], st, hist)

    for conv in convos:
        st = empty_state(conv.get("initial_state"))
        hist = []
        for turn in conv["turns"]:
            await one_turn(turn["text"], st, hist)

    print(f"\n=======================================================")
    print(f"🎉 Wrote {n} Q/A pairs to {out_path} ({errors} errors)")
    print(f"=======================================================\n", flush=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", default=DEFAULT_OUT)
    p.add_argument("--sleep", type=float, default=2.0)
    p.add_argument("--limit", type=int, default=None, help="Cap questions for quick testing")
    p.add_argument("--model", default="openai/gpt-oss-120b", help="Groq model to evaluate")
    args = p.parse_args()
    asyncio.run(run(args.out, args.sleep, args.limit, args.model))


if __name__ == "__main__":
    main()
