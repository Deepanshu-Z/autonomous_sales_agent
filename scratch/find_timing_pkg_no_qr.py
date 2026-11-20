import sys, os, json
sys.path.insert(0, os.path.abspath('.'))
from dotenv import load_dotenv
load_dotenv()
from src.services.supabase_client import supabase

res = supabase.table('user_session_state').select('*').limit(200).execute()
records = res.data or []

print('=== LEADS WITH TIMING & PACKAGE BUT NO QR ===')
for r in records:
    st = r.get('state')
    if isinstance(st, str):
        try:
            st = json.loads(st)
        except Exception:
            st = {}
    elif not isinstance(st, dict):
        st = r
    
    phone = r.get('phone')
    timing = st.get('timing') or st.get('selected_timing')
    pkg = st.get('package') or st.get('selected_duration')
    teacher = st.get('teacher') or st.get('selected_teacher')
    qr = st.get('qr_code_sent') or st.get('is_qr_sent')
    course = st.get('course')
    
    if timing and pkg and not qr:
        print(f"Phone: {phone:15} | Course: {str(course):12} | Timing: {str(timing):22} | Pkg: {str(pkg):10} | Teacher: {str(teacher):15}")
