import json
import subprocess

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import memory
from config import settings
from ollama_client import stream_chat
from phase2.whatsapp_bills import summarize_stream

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


@app.delete("/api/memory/{session_id}")
def reset_memory(session_id: str):
    memory.clear_session(session_id)
    return {"status": "cleared", "session_id": session_id}


# ---------- Bills (Phase 2) ----------


class BillRequest(BaseModel):
    directory: str
    start_date: str | None = None
    end_date: str | None = None
    sender: str | None = None
    read_photos: bool = False
    offset: float = 0.0


@app.post("/api/bills/summarize")
def bills_summarize(req: BillRequest):
    def event_stream():
        try:
            for event in summarize_stream(
                req.directory,
                req.start_date,
                req.end_date,
                req.sender,
                req.read_photos,
                req.offset,
            ):
                yield json.dumps(event) + "\n"
        except FileNotFoundError as e:
            yield json.dumps({"type": "error", "detail": str(e)}) + "\n"

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")


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
        raise HTTPException(status_code=400, detail="No folder selected")
    return {"path": result.stdout.strip()}


# ---------- Manual entries page ----------


class EntryRequest(BaseModel):
    start_date: str  # "YYYY-MM-DD"
    end_date: str  # "YYYY-MM-DD"
    total_amount: float


@app.post("/api/entries")
def create_entry(req: EntryRequest):
    entry_id = memory.add_entry(req.start_date, req.end_date, req.total_amount)
    return {"id": entry_id}


@app.get("/api/entries")
def get_entries():
    return memory.list_entries()


@app.delete("/api/entries/{entry_id}")
def remove_entry(entry_id: int):
    memory.delete_entry(entry_id)
    return {"status": "deleted"}


# ---------- Static frontend ----------
app.mount("/", StaticFiles(directory="static", html=True), name="static")
