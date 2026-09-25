"""
Parses a WhatsApp chat export folder (as produced by "Export Chat" on WhatsApp).

Expects, inside the given directory:
  - `_chat.txt`   with lines like: [06/06/25, 09:35:55] X: +145 rs vegetables
  - image files whose name ENDS with a timestamp like 2026-05-18-10-21-53
    (these are treated as "photo bills" — amounts can't be read from the
    filename, so they're listed separately for now; OCR can be plugged in later)

Handles multi-line WhatsApp messages, e.g.:
  [24/09/26, 09:46:50] X: +130 rs Vegetables
  +39 rs milk
This is treated as ONE message with two bill lines -> two entries, same timestamp.

Only messages from a given sender are counted (case-insensitive, trimmed match
against the sender name as it appears in _chat.txt before the colon).

Date format in _chat.txt is assumed DD/MM/YY (standard WhatsApp India export).
Adjust DATE_FMT below if your export uses a different format (check one line
of your real _chat.txt to confirm).
"""
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# --- tune these if your export differs ---
DATE_FMT = "%d/%m/%y %H:%M:%S"
LINE_RE = re.compile(r"^\[(\d{2}/\d{2}/\d{2}), (\d{2}:\d{2}:\d{2})\] ([^:]+): (.*)$")
IMAGE_TS_RE = re.compile(r"(\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2})\.\w+$")
IMAGE_TS_FMT = "%Y-%m-%d-%H-%M-%S"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic"}

# Matches amounts like: "+145 rs ...", "rs 145", "₹145", "145 rs"
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
    photo_bills: list[dict] = field(default_factory=list)  # filename + timestamp, unread amounts
    unparsed_lines: int = 0


def _extract_amount(line: str) -> float | None:
    """Only treat a line as a bill if it clearly starts with a money marker
    (+, rs, ₹) — avoids accidentally summing random numbers in chat."""
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
    """Groups continuation lines into their parent message. WhatsApp exports
    give no marker for line 2+ of a multi-line message, so any line that
    doesn't match the `[date, time] sender:` header is treated as a
    continuation of the message currently being built.
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
                    current_lines.append(line)  # continuation of the current message
        flush()

    return messages


def summarize(
    directory: str,
    start_date: str | None = None,  # "YYYY-MM-DD"
    end_date: str | None = None,    # "YYYY-MM-DD", inclusive
    sender: str | None = None,      # only count messages from this sender; None/"" = everyone
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

    # --- parse text messages (multi-line + sender aware) ---
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

    # --- find photo bills in range (amounts not extracted — flagged for OCR later) ---
    # Note: photo attachments in _chat.txt appear as a line like
    # "[date, time] sender: <attached: FILE.jpg>", so sender filtering for
    # photos isn't wired yet — all photo bills in the date range are listed
    # regardless of sender. Flag if you want this filtered too.
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
        if _in_range(ts, start, end):
            summary.photo_bills.append({"filename": img.name, "timestamp": ts.isoformat()})

    return summary