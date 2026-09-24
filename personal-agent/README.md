# Personal Agent — Phase 1 + 2

## Setup

```bash
cd personal-agent
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# in another terminal, make sure your model is pulled:
ollama pull gemma4:e4b
```

## Run

```bash
uvicorn main:app --reload --port 8000
```

Open http://localhost:8000 in your browser.

## Swapping models

Create a `.env` file (or edit `config.py` defaults):

```
MODEL_NAME=qwen2.5:7b-instruct
```

No other code changes needed — `ollama_client.py` always reads `settings.model_name`.

## Phase 1 — Chat

- `/api/chat` (POST): streams the model's reply, and persists both the user
  message and the reply to SQLite (`data/agent_memory.db`), keyed by
  `session_id`. Each request rebuilds context from the last N messages
  (`HISTORY_WINDOW` in config, default 20).
- `/api/history/{session_id}` (GET): full history for a session.
- Frontend "Chat" tab talks to both.

## Phase 2 — WhatsApp bill totals

- Point the "Bills" tab at the absolute path of your exported WhatsApp chat
  folder (the one containing `_chat.txt` and the photo attachments).
- Optional start/end date filters (inclusive).
- Backend (`phase2/whatsapp_bills.py`) parses `_chat.txt` line by line,
  extracts messages that start with `+`, `rs`, `₹`, or `inr`, pulls the
  numeric amount, and sums them.
- Photo bills (images whose filename ends in a `YYYY-MM-DD-HH-MM-SS`
  timestamp) that fall in the date range are listed separately — their
  amounts aren't read yet (no OCR wired up). Total does **not** include them.

### Known assumptions to double check against your real export

- Date format in `_chat.txt` is assumed `DD/MM/YY` (standard for WhatsApp
  India exports). If yours differs, edit `DATE_FMT` in
  `phase2/whatsapp_bills.py`.
- Only lines starting with a money marker (`+`, `rs`, `₹`, `inr`) are treated
  as bills, to avoid accidentally summing unrelated numbers in chat. If X's
  messages sometimes omit the marker, tell me the real message formats and
  I'll widen the regex.
- Multi-line WhatsApp messages (a bill description that wraps to a second
  line) are currently skipped — flag if this matters and I'll handle it.

## Next steps (not built yet)

- OCR for photo bills (e.g. local vision model or `pytesseract`) to fold
  those totals in automatically.
- MCP tool-calling loop in `main.py`'s `/api/chat` (currently plain chat,
  no tools wired in yet — that's the natural Phase 3).
- Native macOS folder picker instead of typing the path.
