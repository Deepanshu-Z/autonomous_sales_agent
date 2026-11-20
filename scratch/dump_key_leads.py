import sys
import os
sys.path.insert(0, os.path.abspath("."))
from dotenv import load_dotenv
load_dotenv()
from src.state.chat_history import get_recent_history
from src.state.chat_state import get_user_state

leads = ["919818529417", "919497676630", "918875193058", "919899682723", "918056324171"]

for phone in leads:
    print(f"\n==================== LEAD: {phone} ====================")
    st = get_user_state(phone)
    print("State:", {k: st.get(k) for k in ["course", "stage", "timing", "teacher", "package", "days", "is_qr_sent", "requests_qr"]})
    hist = get_recent_history(phone, 20)
    print(f"Chat Messages ({len(hist)}):")
    for m in hist:
        r = m.get("role")
        c = m.get("content") or ""
        print(f"  [{r}] {c[:120]}")
