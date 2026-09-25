"""
Parses a WhatsApp chat export folder (as produced by "Export Chat" on WhatsApp).

Expects, inside the given directory:
  - `_chat.txt`   with lines like: [06/06/25, 09:35:55] X: +145 rs vegetables
  - image files whose name ENDS with a timestamp like 2026-05-18-10-21-53
    (these are "photo bills" — optionally read via the multimodal model)

Handles multi-line WhatsApp messages, e.g.:
  [24/09/26, 09:46:50] X: +130 rs Vegetables
  +39 rs milk
This is treated as ONE message with two bill lines -> two entries, same timestamp.

Only messages from a given sender are counted (case-insensitive, trimmed match
against the sender name as it appears in _chat.txt before the colon).

Date format in _chat.txt is assumed DD/MM/YY (standard WhatsApp India export).
"""
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ollama_client import extract_bill_amount

# --- tune these if your export differs ---
DATE_FMT = "%d/%m/%y %H:%M:%S"
LINE_RE = re.compile(r"^\[(\d{2}/\d{2}/\d{2}), (\d{2}:\d{2}:\d{2})\] ([^:]+): (.*)$")
IMAGE_TS_RE = re.compile(r"(\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2})\.\w+$")
IMAGE_TS_FMT = "%Y-%m-%d-%H-%M-%S"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic"}

AMOUNT_RE = re.compile(
    r"(?:^\+\s*|(?<!\d))(?:rs\.?|₹|inr)?\s*(\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:rs\.?|₹|inr)?",
    re.IGNORECASE,
)


@dataclass
class BillEntry:
    timestamp: datetime
    amount: float
    raw_message: str


@dataclass
class BillSummary:
    total: float
    text_entries: list[BillEntry] = field(default_factory=list)
    photo_bills: list[dict] = field(default_factory=list)  # filename, timestamp, amount(None if unread)
    unparsed_lines: int = 0


def _extract_amount(line: str) -> float | None:
    stripped = line.strip()
    if not re.match(r"^(\+|rs\.?|₹|inr)\b", stripped, re.IGNORECASE):
        return None
    m = AMOUNT_RE.search(stripped)
    if not m:
        return None
    return float(m.group(1).replace(",", ""))


def _in_range(ts: datetime, start: datetime | None, end: datetime | None) -> bool:
    if start and ts < start:
        return False
    if end and ts > end:
        return False
    return True


def _read_messages(chat_file: Path) -> list[tuple[datetime, str, str]]:
    """Groups continuation lines into their parent message.
    Returns list of (timestamp, sender, full_message_text)."""
    messages: list[tuple[datetime, str, str]] = []
    current_ts: datetime | None = None
    current_sender: str = ""
    current_lines: list[str] = []

    def flush():
        if current_ts is not None and current_lines:
            messages.append((current_ts, current_sender, "\n".join(current_lines)))

    with open(chat_file, "r", encoding="utf-8", errors="ignore") as f:
        for raw_line in f:
            line = raw_line.rstrip("\n")
            m = LINE_RE.match(line)
            if m:
                flush()
                date_str, time_str, sender, message = m.groups()
                try:
                    current_ts = datetime.strptime(f"{date_str} {time_str}", DATE_FMT)
                    current_sender = sender.strip()
                    current_lines = [message]
                except ValueError:
                    current_ts = None
                    current_lines = []
            else:
                if current_ts is not None and line.strip():
                    current_lines.append(line)
        flush()

    return messages


def summarize(
    directory: str,
    start_date: str | None = None,
    end_date: str | None = None,
    sender: str | None = None,
    read_photos: bool = False,
) -> BillSummary:
    dir_path = Path(directory).expanduser()
    if not dir_path.is_dir():
        raise FileNotFoundError(f"Directory not found: {dir_path}")

    chat_file = dir_path / "_chat.txt"
    if not chat_file.is_file():
        raise FileNotFoundError(f"_chat.txt not found in {dir_path}")

    start = datetime.strptime(start_date, "%Y-%m-%d") if start_date else None
    end = (
        datetime.strptime(end_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        if end_date
        else None
    )
    sender_filter = sender.strip().lower() if sender and sender.strip() else None

    summary = BillSummary(total=0.0)

    for ts, msg_sender, message in _read_messages(chat_file):
        if not _in_range(ts, start, end):
            continue
        if sender_filter is not None and msg_sender.strip().lower() != sender_filter:
            continue
        for sub_line in message.split("\n"):
            amount = _extract_amount(sub_line)
            if amount is not None:
                summary.text_entries.append(BillEntry(ts, amount, sub_line.strip()))
                summary.total += amount

    # Photo bills — sender filtering not applied here yet (see note in README)
    for img in dir_path.iterdir():
        if img.suffix.lower() not in IMAGE_EXTS:
            continue
        m = IMAGE_TS_RE.search(img.name)
        if not m:
            continue
        try:
            ts = datetime.strptime(m.group(1), IMAGE_TS_FMT)
        except ValueError:
            continue
        if not _in_range(ts, start, end):
            continue

        entry = {"filename": img.name, "timestamp": ts.isoformat(), "amount": None}
        if read_photos:
            amount = extract_bill_amount(str(img))
            entry["amount"] = amount
            if amount is not None:
                summary.total += amount
        summary.photo_bills.append(entry)

    return summary