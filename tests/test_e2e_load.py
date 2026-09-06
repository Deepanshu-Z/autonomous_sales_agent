import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
"""
test.py — Full End-to-End (E2E) AI Generation Load Tester.
Simulates N simultaneous users sending messages to the test webhook (bypassing signature check),
and tracks real-time background worker execution, vector RAG search, OpenAI LLM response generation,
and complete end-to-end latency. Automatically exports detailed results to test_results_100_users.csv.

Usage:
    python test.py                    (defaults to 100 simultaneous users)
    python test.py 5                  (or specify 5 users for fast test)
    python test.py 30 http://localhost:8000/test-webhook
"""

import sys
import time
import json
import csv
import random
import asyncio
import httpx
import os
from dotenv import load_dotenv
from src.services.redis_client import get_redis_connection
from src.state.chat_state import get_user_state

load_dotenv()
redis_conn = get_redis_connection()

DEFAULT_WEBHOOK_URL = "http://localhost:8000/test-webhook"
NUM_USERS = int(sys.argv[1]) if len(sys.argv) > 1 else 100
WEBHOOK_URL = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_WEBHOOK_URL
OUTPUT_CSV_FILE = "test_results_100_users.csv"

TARGET_AD_ID = os.getenv("TARGET_AD_ID") or "123456789"
TARGET_TEXT = "hello! can i get more info on yoga classes?"

SAMPLE_MESSAGES = [
    "Hi",
    "Yes",
    "Other timings",
    "5 6 pm",
    "What are the fees?",
    "Do you have a demo class video?",
    "3 months",
    "1 year package",
    "Is this online live on Zoom?",
    "Teacher details kya hai?",
    "hello! can i get more info on yoga classes?"
]

def is_simulated_target_ad(user_id: int, message_text: str) -> bool:
    """Returns True if this simulated user is targeted (odd user_id or matching text)."""
    if message_text.strip().lower() == TARGET_TEXT:
        return True
    return user_id % 2 == 1


def generate_payload(user_id: int) -> tuple[dict, str, str]:
    """Generates an Interakt-compliant webhook payload for a unique simulated phone number."""
    phone_number = f"919800{user_id:06d}"  # Unique phone: 919800000001, 919800000002...
    
    # Let some messages match TARGET_TEXT exactly
    if user_id % 5 == 0:
        message_text = "Hello! Can I get more info on Yoga classes?"
    else:
        message_text = SAMPLE_MESSAGES[(user_id - 1) % len(SAMPLE_MESSAGES)]

    # Odd user IDs simulate target ad referrals, even user IDs simulate other non-target ads
    if is_simulated_target_ad(user_id, message_text):
        referral = {
            "source_id": TARGET_AD_ID,
            "source_type": "ad",
            "source_url": f"https://fb.com/l.php?ad_id={TARGET_AD_ID}"
        }
    else:
        referral = {
            "source_id": "999999999_wrong_ad",
            "source_type": "ad",
            "source_url": "https://fb.com/l.php?ad_id=999999999_wrong_ad"
        }

    payload = {
        "type": "message_received",
        "data": {
            "customer": {
                "country_code": "91",
                "phone_number": phone_number,
                "channel_phone_number": phone_number
            },
            "message": {
                "message": message_text,
                "referral": referral
            }
        }
    }
    return payload, phone_number, message_text


async def send_single_webhook(client: httpx.AsyncClient, user_id: int) -> dict:
    """Fires a single HTTP POST webhook request and records precise timing."""
    payload, phone, message_text = generate_payload(user_id)
    sent_at = time.time()

    try:
        response = await client.post(
            WEBHOOK_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=200.0
        )
        latency_ms = (time.time() - sent_at) * 1000.0  # in ms
        return {
            "user_id": user_id,
            "phone": phone,
            "user_message": message_text,
            "status_code": response.status_code,
            "enqueue_latency_ms": round(latency_ms, 2),
            "sent_at": sent_at,
            "error": None
        }
    except Exception as e:
        latency_ms = (time.time() - sent_at) * 1000.0
        return {
            "user_id": user_id,
            "phone": phone,
            "user_message": message_text,
            "status_code": None,
            "enqueue_latency_ms": round(latency_ms, 2),
            "sent_at": sent_at,
            "error": str(e)
        }


def check_ai_response(phone: str) -> dict | None:
    """Checks Redis history for the LATEST generated assistant response for a phone number."""
    key = f"history:{phone}"
    try:
        cached = redis_conn.lrange(key, 0, -1)
        if cached:
            # Scan backwards to get the most recent assistant message from current test
            for item in reversed(cached):
                turn = json.loads(item)
                if turn.get("role") == "assistant":
                    return turn
    except Exception:
        pass
    return None


async def monitor_e2e_ai_completion(webhook_results: list[dict], timeout_sec: float = 120.0) -> dict[str, dict]:
    """Polls Redis history in real-time until targeted AI responses complete, computing accurate latency."""
    print("\n" + "=" * 65)
    print("⚙️ PHASE 2: Monitoring End-to-End AI Generation Across Background Workers...")
    print("=" * 65)

    start_monitor_time = time.time()
    completed_ai_responses = {}
    
    phone_sent_times = {r["phone"]: r["sent_at"] for r in webhook_results if r["status_code"] == 200}
    
    # We only monitor and wait for phone numbers that originate from the Target Ad or match the Target Text
    target_phones = {
        r["phone"]
        for r in webhook_results
        if r["status_code"] == 200 and is_simulated_target_ad(r["user_id"], r["user_message"])
    }
    pending_phones = set(target_phones)

    print(f"Waiting for {len(pending_phones)} target ad users to receive replies. (Ignored users will not be checked).")

    last_report_count = 0

    while pending_phones and (time.time() - start_monitor_time) < timeout_sec:
        newly_completed = []
        for phone in pending_phones:
            ai_turn = check_ai_response(phone)
            if ai_turn:
                completed_time = time.time()
                sent_time = phone_sent_times.get(phone, start_monitor_time)
                
                # Use server response_time_sec if present, otherwise calculate exact wall-clock latency
                server_lat = ai_turn.get("response_time_sec")
                calc_lat = round(completed_time - sent_time, 2)
                final_latency = server_lat if (server_lat is not None and server_lat > 0) else calc_lat

                completed_ai_responses[phone] = {
                    "reply": ai_turn.get("content"),
                    "response_time_sec": final_latency,
                    "completed_at": completed_time
                }
                newly_completed.append(phone)

        for phone in newly_completed:
            pending_phones.remove(phone)

        current_completed = len(completed_ai_responses)
        if current_completed > last_report_count:
            elapsed = time.time() - start_monitor_time
            print(f" ⌛ Progress: [{current_completed}/{len(target_phones)}] AI Responses Completed ({elapsed:.1f}s elapsed)...")
            last_report_count = current_completed

        if pending_phones:
            await asyncio.sleep(0.3)

    return completed_ai_responses


def save_test_results_to_csv(webhook_results: list[dict], ai_results: dict[str, dict], filename: str = OUTPUT_CSV_FILE):
    """Saves complete load test trace and AI responses into a CSV file."""
    fieldnames = [
        "user_id",
        "phone",
        "user_message",
        "status_code",
        "enqueue_latency_ms",
        "ai_reply",
        "response_time_sec",
        "funnel_stage",
        "status"
    ]

    rows = []
    for item in webhook_results:
        phone = item["phone"]
        user_id = item["user_id"]
        ai_data = ai_results.get(phone)
        is_target = is_simulated_target_ad(user_id, item["user_message"])

        try:
            state = get_user_state(phone)
            stage = state.get("stage", "UNKNOWN")
        except Exception:
            stage = "UNKNOWN"

        if ai_data and ai_data.get("reply"):
            status = "SUCCESS"
            reply = ai_data.get("reply", "")
            resp_time = ai_data.get("response_time_sec")
        elif not is_target:
            status = "IGNORED (Correctly skipped by Ad Routing)"
            reply = ""
            resp_time = None
        else:
            status = "FAILED/TIMEOUT" if item["status_code"] == 200 else "WEBHOOK_FAILED"
            reply = ""
            resp_time = None

        rows.append({
            "user_id": user_id,
            "phone": phone,
            "user_message": item["user_message"],
            "status_code": item["status_code"],
            "enqueue_latency_ms": item["enqueue_latency_ms"],
            "ai_reply": reply,
            "response_time_sec": resp_time,
            "funnel_stage": stage,
            "status": status
        })

    rows.sort(key=lambda x: x["user_id"])

    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n📁 Full Load Test Results successfully saved to: {filename}")


async def run_e2e_load_test():
    """Executes full End-to-End load test: Webhook Ingestion + AI Generation Tracking + CSV File Creation."""
    print("=" * 65)
    print(f"🚀 STARTING END-TO-END (E2E) AI LOAD TEST: {NUM_USERS} Simultaneous Users")
    print(f"🎯 Target Webhook URL : {WEBHOOK_URL}")
    print("=" * 65)

    # Phase 1: Fire Webhook Requests Simultaneously
    print(f"\n⚡ PHASE 1: Firing {NUM_USERS} Webhooks Simultaneously...")
    limits = httpx.Limits(max_keepalive_connections=NUM_USERS, max_connections=NUM_USERS + 20)
    
    start_e2e_time = time.time()
    async with httpx.AsyncClient(limits=limits) as client:
        tasks = [send_single_webhook(client, i + 1) for i in range(NUM_USERS)]
        webhook_results = await asyncio.gather(*tasks)

    webhook_time = time.time() - start_e2e_time
    successful_webhooks = [r for r in webhook_results if r["status_code"] == 200]

    print(f" ✅ Webhook Ingestion Complete: {len(successful_webhooks)}/{NUM_USERS} HTTP 200 OK in {webhook_time:.2f}s")

    if not successful_webhooks:
        print("❌ Error: No webhooks were successfully received by the server. Ensure uvicorn is running!")
        save_test_results_to_csv(webhook_results, {})
        return

    # Phase 2: Monitor AI Generation
    ai_results = await monitor_e2e_ai_completion(webhook_results, timeout_sec=120.0)
    total_e2e_time = time.time() - start_e2e_time

    # Phase 3: Save CSV File
    save_test_results_to_csv(webhook_results, ai_results, OUTPUT_CSV_FILE)

    # Phase 4: Calculate Performance Summary
    latencies = [data["response_time_sec"] for data in ai_results.values() if data.get("response_time_sec") is not None]
    avg_latency = sum(latencies) / len(latencies) if latencies else 0
    min_latency = min(latencies) if latencies else 0
    max_latency = max(latencies) if latencies else 0

    print("\n" + "=" * 65)
    print("📊 END-TO-END (E2E) AI PERFORMANCE SUMMARY")
    print("=" * 65)
    print(f" 📥 Simultaneous Users      : {NUM_USERS}")
    print(f" ✅ Webhooks Accepted (200 OK): {len(successful_webhooks)}")
    print(f" 🤖 AI Responses Completed  : {len(ai_results)} / {NUM_USERS // 2 + (1 if NUM_USERS % 2 != 0 else 0)}")
    print(f" ⏱️ End-to-End Total Time   : {total_e2e_time:.2f} seconds")
    print("-" * 65)
    if latencies:
        print(f" ⚡ Average AI Generation Time: {avg_latency:.2f} seconds")
        print(f" 🏎️ Fastest AI Generation Time: {min_latency:.2f} seconds")
        print(f" 🐢 Slowest AI Generation Time: {max_latency:.2f} seconds")
    print("=" * 65)

    # Print Sample AI Responses
    print("\n💬 SAMPLE AI RESPONSES GENERATED UNDER LOAD:")
    print("-" * 65)
    
    # Print samples, selecting some target and some ignored
    sample_phones = [r["phone"] for r in webhook_results[:5]]
    for i, phone in enumerate(sample_phones, 1):
        item = next(r for r in webhook_results if r["phone"] == phone)
        data = ai_results.get(phone)
        is_target = is_simulated_target_ad(item["user_id"], item["user_message"])
        
        print(f"Sample {i} [{phone}]:")
        print(f"  User Message: \"{item['user_message']}\"")
        if is_target:
            if data and data.get("reply"):
                print(f"  AI Reply    : \"{data['reply']}\"")
                print(f"  Latency     : {data.get('response_time_sec'):.2f}s")
            else:
                print("  AI Reply    : (Timeout/Error)")
        else:
            print("  AI Reply    : (Ignored - Not matching Target Ad)")
        print("-" * 65)


if __name__ == "__main__":
    asyncio.run(run_e2e_load_test())
