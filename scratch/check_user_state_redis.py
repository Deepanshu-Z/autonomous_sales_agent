import sys
import os
sys.path.insert(0, os.path.abspath("."))
import json
from dotenv import load_dotenv

load_dotenv()

from src.services.redis_client import get_redis_connection

r = get_redis_connection()
phone = "917678368328"
raw = r.get(f"user_state:{phone}")
print(f"user_state:{phone} ->", raw)
