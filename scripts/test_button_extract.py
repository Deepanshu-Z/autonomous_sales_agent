import asyncio, sys, json, os
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.abspath("."))
from src.core.intent_classifier import extract_intent_and_state_changes

async def test():
    raw_str = json.dumps({"type": "button_reply", "button_reply": {"id": "btn_2", "title": "Yes, help me"}})
    res = await extract_intent_and_state_changes(text=raw_str, state={'stage': 'NEW'}, history=[])
    print('Raw JSON extraction:', res)
    
    res2 = await extract_intent_and_state_changes(text='Yes, help me', state={'stage': 'NEW'}, history=[])
    print('Plain text extraction:', res2)

asyncio.run(test())
