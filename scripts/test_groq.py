import os
import sys
import time
sys.stdout.reconfigure(encoding='utf-8')
from dotenv import load_dotenv
load_dotenv()
from groq import Groq

client = Groq()

query = "Face Yoga ki classes ke kitne paise lagte hain?"
print(f"Testing Groq model: openai/gpt-oss-120b with query: '{query}'\n")

start_time = time.time()
completion = client.chat.completions.create(
    model="openai/gpt-oss-120b",
    messages=[
      {
        "role": "system",
        "content": "You are a warm, helpful sales counsellor for Sensationz. Reply in strict JSON: {\"reply\": \"...\", \"followup\": \"...\"}"
      },
      {
        "role": "user",
        "content": query
      }
    ],
    temperature=1,
    max_completion_tokens=2048,
    top_p=1,
    reasoning_effort="medium",
    stream=True,
    stop=None
)

full_response = ""
for chunk in completion:
    text = chunk.choices[0].delta.content or ""
    full_response += text
    print(text, end="", flush=True)

elapsed = time.time() - start_time
print(f"\n\n⏱️ Total Streaming Latency: {elapsed:.2f} seconds")
