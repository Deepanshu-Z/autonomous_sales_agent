"""
Deep eval runner: 80 independent questions + 20 x 4-turn conversations.

Calls production handle_incoming_message (extractor + reply LLM).
Writes only Qn/An pairs to test_result.txt at repo root.

Usage (from repo root):
    python tests/eval_deep/run_eval.py
    python tests/eval_deep/run_eval.py --limit 4
    python tests/eval_deep/run_eval.py --sleep 0.8
"""

from __future__ import annotations

import argparse
import asyncio
import os
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
from src.core.conversation_handler import handle_incoming_message
DEFAULT_OUT = os.path.join(ROOT, "test_result.txt")


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


async def _ask(
    text: str,
    state: Dict[str, Any],
    history: List[Dict[str, str]],
) -> Dict[str, Any]:
    return await handle_incoming_message(
        user_message=text,
        session_state=state,
        conversation_history=history,
        course_id=state.get("course"),
    )


async def run(out_path: str, sleep_s: float, limit: Optional[int]) -> None:
    lines: List[str] = []
    n = 0
    errors = 0

    independent = INDEPENDENT if not limit else INDEPENDENT[:limit]
    convos = CONVERSATIONS if not limit else CONVERSATIONS[: max(1, limit // 4)]

    with open(out_path, "w", encoding="utf-8") as f:
        pass

    async def one_turn(text: str, state: Dict[str, Any], history: List[Dict[str, str]]) -> str:
        nonlocal n, errors
        n += 1
        qn = n
        print(f"[{qn}] {text[:90]}{'...' if len(text) > 90 else ''}", flush=True)
        t0 = time.perf_counter()
        try:
            result = await _ask(text, state, history)
            elapsed = time.perf_counter() - t0
            print(f"    ok {elapsed:.1f}s", flush=True)
            new_state = result.get("new_state") or state
            state.clear()
            state.update(new_state)
            answer = _format_answer(result)
        except Exception as exc:
            errors += 1
            elapsed = time.perf_counter() - t0
            print(f"    FAIL {elapsed:.1f}s {exc}", flush=True)
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

    print(f"\nWrote {n} Q/A pairs to {out_path} ({errors} errors)", flush=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", default=DEFAULT_OUT)
    p.add_argument("--sleep", type=float, default=0.6)
    p.add_argument("--limit", type=int, default=None, help="Cap independent questions; also shrinks convos")
    args = p.parse_args()
    asyncio.run(run(args.out, args.sleep, args.limit))


if __name__ == "__main__":
    main()
