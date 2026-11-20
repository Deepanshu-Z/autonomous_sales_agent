import sys, os
sys.path.insert(0, os.path.abspath("."))
from dotenv import load_dotenv
load_dotenv()
from src.state.chat_history import get_recent_history

leads = ["917780358487", "918398842476", "919967286863", "918196944851", "917011064774", "919964234119"]

for ph in leads:
    print(f"\n==================== LEAD: {ph} ====================")
    hist = get_recent_history(ph, 15)
    for m in hist:
        r = m.get("role")
        c = (m.get("content") or "").replace("\n", " ")
        print(f"[{r}]: {c[:140]}")
