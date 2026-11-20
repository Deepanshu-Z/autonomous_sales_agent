import httpx

# Send a test webhook to the live server
payload = {
    "type": "message_received",
    "data": {
        "customer": {
            "phone_number": "7678368328",
            "country_code": "+91"
        },
        "message": {
            "text": "ping test"
        }
    }
}

try:
    r = httpx.post("https://salesagent.sensationzperformingarts.com/test-webhook", json=payload, timeout=10.0)
    print("Status:", r.status_code, r.text)
except Exception as e:
    print("Error:", e)
