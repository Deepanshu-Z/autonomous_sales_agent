"""Self-check: 3h follow-up sends the raw class video (text kept as fallback). Run: python scripts/check_3h_video.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["LEGACY_FOLLOWUP"] = "false"
from src.services import followup_service as fs

fs.get_next_agent_email = lambda: ("x@y", None)  # not hit for counts 1-3

def d(course, count, already=False):
    return fs.get_followup_decision("911234567890", count, course, course, False, [], class_video_already_sent=already)

k = d("kathak", 2)
assert k.video_url and k.video_url.endswith(".mp4"), k
assert k.message, "text fallback must still be present"
assert d("kathak", 2, already=True).video_url is None, "video already sent in chat -> text"
assert d("face_yoga", 2).video_url is None and d("face_yoga", 2).message, "no video course -> text"
assert d(None, 2).video_url is None, "general -> text"
assert d("kathak", 1).video_url is None and d("kathak", 3).video_url is None, "only the 3h step"
print("OK: 3h class-video follow-up checks passed")
