"""
Simple SQLite storage: chat memory + manual bill-period entries.
No extra services to run, no extra deps.
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from config import settings

Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def get_conn():
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,          -- 'user' | 'assistant' | 'system'
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_session ON messages(session_id, id)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS manual_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                start_date TEXT NOT NULL,    -- "YYYY-MM-DD"
                end_date TEXT NOT NULL,      -- "YYYY-MM-DD"
                total_amount REAL NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )


def add_message(session_id: str, role: str, content: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (session_id, role, content, datetime.now(timezone.utc).isoformat()),
        )


def get_history(session_id: str, limit: int | None = None) -> list[dict]:
    limit = limit or settings.history_window
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT role, content FROM (
                SELECT role, content, id FROM messages
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT ?
            ) ORDER BY id ASC
            """,
            (session_id, limit),
        ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in rows]


def list_sessions() -> list[str]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT session_id FROM messages ORDER BY session_id"
        ).fetchall()
    return [r["session_id"] for r in rows]


def clear_session(session_id: str) -> None:
    """Deletes all stored messages for a session — used by 'Reset Memory'."""
    with get_conn() as conn:
        conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))


def add_entry(start_date: str, end_date: str, total_amount: float) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO manual_entries (start_date, end_date, total_amount, created_at) VALUES (?, ?, ?, ?)",
            (
                start_date,
                end_date,
                total_amount,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        return cur.lastrowid


def list_entries() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, start_date, end_date, total_amount FROM manual_entries ORDER BY start_date"
        ).fetchall()
    return [dict(r) for r in rows]


def delete_entry(entry_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM manual_entries WHERE id = ?", (entry_id,))


init_db()
