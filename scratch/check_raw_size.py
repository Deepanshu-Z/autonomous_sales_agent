import httpx

url = "https://res.cloudinary.com/drqsicr04/video/upload/v1795083799/Kathak_181112_1_ei7qih.mp4"
try:
    r = httpx.head(url, follow_redirects=True)
    size_mb = int(r.headers.get("content-length", 0)) / (1024 * 1024)
    print(f"Raw Size without transformation: {size_mb:.2f} MB")
except Exception as e:
    print("Error:", e)
