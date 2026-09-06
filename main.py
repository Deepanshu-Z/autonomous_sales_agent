"""
main.py — Root application entrypoint.
Exposes the FastAPI `app` instance for uvicorn, start.py, and PM2.
"""

from src.api import create_app

app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
