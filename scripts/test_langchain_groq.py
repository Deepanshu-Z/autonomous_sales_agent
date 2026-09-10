import os, sys
sys.stdout.reconfigure(encoding='utf-8')
from dotenv import load_dotenv
load_dotenv()
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

groq_key = os.getenv('GROQ_API_KEY')
print("Testing ChatOpenAI with Groq base_url and model='openai/gpt-oss-120b'...")

try:
    llm = ChatOpenAI(
        model='openai/gpt-oss-120b',
        api_key=groq_key,
        base_url='https://api.groq.com/openai/v1',
        temperature=0.2,
        model_kwargs={'response_format': {'type': 'json_object'}},
    )

    res = llm.invoke([
        SystemMessage(content='You are an AI assistant. Return JSON object with keys "reply" and "followup".'),
        HumanMessage(content='Face Yoga classes ki fees kitni hai?')
    ])
    print("SUCCESS! Response:")
    print(res.content)
except Exception as e:
    print("ERROR:", e)
