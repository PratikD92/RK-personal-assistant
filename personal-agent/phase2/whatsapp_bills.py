"""
Parses a WhatsApp chat export folder (as produced by "Export Chat" on WhatsApp).

Expects, inside the given directory:
  - `_chat.txt`   with lines like: [06/06/25, 09:35:55] X: +145 rs vegetables
  - image files whose name ENDS with a timestamp like 2026-05-18-10-21-53
    (these are "photo bills" — optionally read via the multimodal model)

Handles multi-line WhatsApp messages (a bill description that continues on
the next line with no [date, time] header) — each sub-line is scanned for
its own amount.

A line starting with "-" (e.g. "-70 rs return seviyan") is a refund/return
and SUBTRACTS from the total. A line containing the word "pending"
(case-insensitive) is excluded from the total by default, but its amount is
still extracted and returned separately under "ignored" — the frontend lets
you review these and manually include specific ones later.

Only messages from a given sender are counted for text bills (case-insensitive,
trimmed match against the sender name as it appears in _chat.txt before the colon).
Ignored/pending detection applies only to text messages, not photo bills.

summarize_stream() is a generator: it yields progress events while photo
bills are being read by the model (one model call per photo is slow), then a
single final "result" event containing the combined, time-sorted list of
message + photo bills, the ignored ("pending") message candidates, and the
grand total (including the manual offset).

Date format in _chat.txt is assumed DD/MM/YY (standard WhatsApp India export).
"""

import re
from datetime import datetime
from pathlib import Path
from typing import Iterator

from ollama_client import extract_bill_amount

DATE_FMT = "%d/%m/%y %H:%M:%S"
LINE_RE = re.compile(r"^\[(\d{2}/\d{2}/\d{2}), (\d{2}:\d{2}:\d{2})\] ([^:]+): (.*)$")
IMAGE_TS_RE = re.compile(r"(\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2})\.\w+$")
IMAGE_TS_FMT = "%Y-%m-%d-%H-%M-%S"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic"}

AMOUNT_RE = re.compile(
    r"(?:^[+-]\s*|(?<!\d))(?:rs\.?|₹|inr)?\s*(\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:rs\.?|₹|inr)?",
    re.IGNORECASE,
)


def _extract_amount(line: str, ignore_pending: bool = False) -> float | None:
    """Returns the signed amount for a bill-like line, or None if it doesn't
    look like a bill line at all. If ignore_pending=False (default), a line
    containing "pending" returns None even if it otherwise looks like a bill
    — set ignore_pending=True to force extraction anyway (used to compute
    the "would-be" amount for the Ignored list)."""
    stripped = line.strip()
    if not ignore_pending and "pending" in stripped.lower():
        return None
    if not re.match(r"^([+-]|rs\.?|₹|inr)\b", stripped, re.IGNORECASE):
        return None
    m = AMOUNT_RE.search(stripped)
    if not m:
        return None
    amount = float(m.group(1).replace(",", ""))
    if stripped.startswith("-"):
        amount = -amount
    return amount


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


def summarize_stream(
    directory: str,
    start_date: str | None = None,
    end_date: str | None = None,
    sender: str | None = None,
    read_photos: bool = False,
    offset: float = 0.0,
) -> Iterator[dict]:
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

    entries: list[dict] = []
    ignored: list[dict] = []
    total = float(offset)

    for ts, msg_sender, message in _read_messages(chat_file):
        if not _in_range(ts, start, end):
            continue
        if sender_filter is not None and msg_sender.strip().lower() != sender_filter:
            continue
        for sub_line in message.split("\n"):
            amount = _extract_amount(sub_line)
            if amount is not None:
                entries.append(
                    {
                        "timestamp": ts,
                        "type": "message",
                        "amount": amount,
                        "detail": sub_line.strip(),
                    }
                )
                total += amount
            else:
                forced = _extract_amount(sub_line, ignore_pending=True)
                if forced is not None:
                    # was skipped only because of "pending" — candidate for manual include
                    ignored.append(
                        {
                            "timestamp": ts.isoformat(),
                            "amount": forced,
                            "detail": sub_line.strip(),
                        }
                    )

    photo_files: list[tuple[datetime, Path]] = []
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
            photo_files.append((ts, img))

    photo_count = len(photo_files)
    for idx, (ts, img) in enumerate(photo_files, start=1):
        amount = None
        if read_photos:
            yield {"type": "progress", "current": idx, "total": photo_count}
            amount = extract_bill_amount(str(img))
            if amount is not None:
                total += amount
        entries.append(
            {
                "timestamp": ts,
                "type": "photo",
                "amount": amount,
                "detail": img.name,
            }
        )

    entries.sort(key=lambda e: e["timestamp"])
    ignored.sort(key=lambda e: e["timestamp"])

    yield {
        "type": "result",
        "total": total,
        "offset": offset,
        "entries": [{**e, "timestamp": e["timestamp"].isoformat()} for e in entries],
        "ignored": ignored,
    }
