import os
import sys
sys.path.insert(0, os.path.abspath("."))
import json
from dotenv import load_dotenv
load_dotenv()
from src.state.chat_state import get_user_state
from src.state.chat_history import get_recent_history

phone = '917678368328'
state = get_user_state(phone)
print('STATE for 917678368328:')
print(json.dumps(state, indent=2, default=str))

history = get_recent_history(phone, limit=20)
print(f'\nHISTORY COUNT: {len(history)}')
for h in history:
    print(f"[{h.get('role')}] {h.get('content')}")
