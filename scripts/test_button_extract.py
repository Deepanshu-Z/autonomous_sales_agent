import asyncio, sys, json, os
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.abspath("."))
from src.core.intent_classifier import extract_intent_and_state_changes
from src.api.routes import _extract_message_text

async def test():
    btn_dict = {"type": "button_reply", "button_reply": {"id": "btn_2", "title": "Yes, help me"}}
    raw_str = json.dumps(btn_dict)
    
    extracted_from_dict = _extract_message_text(btn_dict)
    extracted_from_json = _extract_message_text({"message": raw_str})
    print(f"Extracted from dict: '{extracted_from_dict}'")
    print(f"Extracted from JSON: '{extracted_from_json}'")
    
    res = await extract_intent_and_state_changes(text=extracted_from_json, state={'stage': 'NEW'}, history=[])
    print('Clean text extraction requests_human_agent:', res.get('requests_human_agent'))
    print('Clean text customer_intent:', res.get('customer_intent'))
    
    agent_msg = await extract_intent_and_state_changes(text="Please connect me to an agent", state={'stage': 'NEW'}, history=[])
    print('Explicit agent request requests_human_agent:', agent_msg.get('requests_human_agent'))
    print('Explicit agent request customer_intent:', agent_msg.get('customer_intent'))

asyncio.run(test())

