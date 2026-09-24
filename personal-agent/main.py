import subprocess
from dataclasses import asdict

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import memory
from config import settings
from ollama_client import stream_chat
from phase2.whatsapp_bills import summarize

app = FastAPI(title="Personal Agent")


# ---------- Chat (Phase 1) ----------

class ChatRequest(BaseModel):
    session_id: str = "default"
    message: str


@app.post("/api/chat")
def chat_endpoint(req: ChatRequest):
    memory.add_message(req.session_id, "user", req.message)
    history = memory.get_history(req.session_id)
    messages = [{"role": "system", "content": settings.system_prompt}, *history]

    def generate():
        full_reply = ""
        for chunk in stream_chat(messages):
            full_reply += chunk
            yield chunk
        memory.add_message(req.session_id, "assistant", full_reply)

    return StreamingResponse(generate(), media_type="text/plain")


@app.get("/api/history/{session_id}")
def get_history(session_id: str):
    return memory.get_history(session_id, limit=1000)


@app.get("/api/sessions")
def get_sessions():
    return memory.list_sessions()


# ---------- Bills (Phase 2) ----------

class BillRequest(BaseModel):
    directory: str
    start_date: str | None = None  # "YYYY-MM-DD"
    end_date: str | None = None


@app.post("/api/bills/summarize")
def bills_summarize(req: BillRequest):
    try:
        result = summarize(req.directory, req.start_date, req.end_date)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return {
        "total": result.total,
        "text_entries": [
            {"timestamp": e.timestamp.isoformat(), "amount": e.amount, "message": e.raw_message}
            for e in result.text_entries
        ],
        "photo_bills": result.photo_bills,
        "unparsed_lines": result.unparsed_lines,
    }


@app.post("/api/pick-folder")
def pick_folder():
    """Opens a native macOS folder picker (via AppleScript) and returns the
    chosen absolute path. Only works when the server runs locally on the
    same Mac with GUI access — which is exactly our setup."""
    script = 'POSIX path of (choose folder with prompt "Select WhatsApp export folder")'
    try:
        result = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True, timeout=120
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=408, detail="Folder picker timed out")
    if result.returncode != 0:
        # non-zero usually means the user hit Cancel
        raise HTTPException(status_code=400, detail="No folder selected")
    return {"path": result.stdout.strip()}


# ---------- Static frontend ----------
app.mount("/", StaticFiles(directory="static", html=True), name="static")
