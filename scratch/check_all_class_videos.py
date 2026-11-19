import httpx

urls = {
    "kathak": "https://res.cloudinary.com/drqsicr04/video/upload/w_720,q_auto:eco,vc_h264/v1795083799/Kathak_181112_1_ei7qih.mp4",
    "ladies_dance": "https://res.cloudinary.com/drqsicr04/video/upload/w_720,q_auto:eco,vc_h264/v1795083808/Anjali_1411_1_mkigtt.mp4",
    "yoga": "https://res.cloudinary.com/drqsicr04/video/upload/w_720,q_auto:eco,vc_h264/v1795083817/Demo_1_genf8n.mp4"
}

for name, u in urls.items():
    try:
        r = httpx.head(u, follow_redirects=True)
        size_mb = int(r.headers.get("content-length", 0)) / (1024 * 1024)
        print(f"{name}: {size_mb:.2f} MB (Status: {r.status_code})")
    except Exception as e:
        print(f"{name} error:", e)
