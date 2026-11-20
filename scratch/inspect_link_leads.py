import sys, os
sys.path.insert(0, os.path.abspath('.'))
from dotenv import load_dotenv
load_dotenv()
from src.state.chat_history import get_recent_history
from src.state.chat_state import get_user_state

for phone in ["919302657495", "919811134113"]:
    print(f"\n==================== LEAD: {phone} ====================")
    st = get_user_state(phone)
    print("State:", {k: st.get(k) for k in ["course", "stage", "timing", "teacher", "package", "days", "is_qr_sent", "qr_id", "last_qr_url"]})
    for m in get_recent_history(phone, 12):
        print(f"[{m.get('role')}]: {m.get('content')}")
