import os, json, webbrowser, threading
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

load_dotenv()
app = FastAPI()

@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    return Path("index.html").read_text(encoding="utf-8")

@app.post("/api/scrape")
async def scrape_zillow(request: Request):
    body = await request.json()
    url = body.get("url", "")
    return JSONResponse({"status": "not implemented", "url": url})

@app.post("/api/analyze-ai")
async def analyze_ai(request: Request):
    body = await request.json()
    return JSONResponse({"status": "not implemented"})

def open_browser():
    webbrowser.open("http://localhost:8000")

if __name__ == "__main__":
    threading.Timer(1.5, open_browser).start()
    uvicorn.run(app, host="127.0.0.1", port=8000)
