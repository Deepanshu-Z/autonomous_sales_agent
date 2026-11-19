import httpx

try:
    r = httpx.get("https://salesagent.sensationzperformingarts.com/health", timeout=10.0)
    print("Health Status:", r.status_code, r.text)
except Exception as e:
    print("Error:", e)
