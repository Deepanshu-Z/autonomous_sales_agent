import sys
import os
sys.path.insert(0, os.path.abspath("."))
import json
from dotenv import load_dotenv

load_dotenv()

from src.services.redis_client import get_redis_connection

r = get_redis_connection()
phone = "917678368328"
state_raw = r.get(f"session:{phone}")
if state_raw:
    state = json.loads(state_raw)
    for k, v in state.items():
        print(f"{k}: {v}")
else:
    print("No redis session found")
