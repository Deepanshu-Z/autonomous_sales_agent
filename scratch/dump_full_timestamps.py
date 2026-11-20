import sys, os
sys.path.insert(0, os.path.abspath("."))
from dotenv import load_dotenv
load_dotenv()
from src.state.chat_history import get_full_history_for_agent

leads = ["917780358487", "918398842476", "919967286863", "918196944851", "917011064774", "919964234119"]

for ph in leads:
    print(f"\n==================== FULL CHAT: {ph} ====================")
    hist = get_full_history_for_agent(ph)
    for m in hist:
        r = m.get("role")
        c = (m.get("message") or m.get("content") or "").replace("\n", " ")
        ts = m.get("created_at") or ""
        print(f"[{ts[:19]}] [{r}]: {c[:110]}")
