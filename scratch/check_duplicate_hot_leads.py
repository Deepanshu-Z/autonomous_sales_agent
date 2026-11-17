from src.services.supabase_client import supabase

for raw in ['9414236483', '7887566141']:
    print(f"\n==========================================")
    print(f"SEARCHING FOR {raw}")
    print(f"==========================================")
    res = supabase.table('user_session_state').select('*').ilike('phone', f'%{raw}%').execute()
    print(f"Found {len(res.data)} matching rows in user_session_state:")
    for row in res.data:
        print(f"  Phone: {row.get('phone')}, UpdatedAt: {row.get('updated_at')}, Stage: {row.get('stage')}, Course: {row.get('course')}, Package: {row.get('package')}, is_qr_sent: {row.get('is_qr_sent')}, qr_id: {row.get('qr_id')}")

    res_chat = supabase.table('chat_history').select('phone, created_at, role, message').ilike('phone', f'%{raw}%').order('created_at', desc=False).execute()
    print(f"Found {len(res_chat.data)} messages in chat_history.")
    if res_chat.data:
        print(f"  First msg at: {res_chat.data[0]['created_at']}")
        print(f"  Last msg at:  {res_chat.data[-1]['created_at']}")
        for m in res_chat.data[:3]:
            print(f"    [{m['created_at']}] {m['role']}: {m['message'][:60]}")
        if len(res_chat.data) > 3:
            print("    ...")
            for m in res_chat.data[-3:]:
                print(f"    [{m['created_at']}] {m['role']}: {m['message'][:60]}")
