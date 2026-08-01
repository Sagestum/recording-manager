import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from .config import DB_PATH, DEFAULT_CHECK_INTERVAL_HOURS

SCHEMA = """
CREATE TABLE IF NOT EXISTS patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern TEXT NOT NULL,
    days INTEGER NOT NULL,
    is_regex INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deletion_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    eventname TEXT NOT NULL,
    filename TEXT NOT NULL,
    pattern TEXT NOT NULL,
    recordingtime INTEGER,
    deleted_at INTEGER NOT NULL,
    success INTEGER NOT NULL,
    message TEXT
);
"""

_DEFAULT_SETTINGS = {
    "check_interval_hours": str(DEFAULT_CHECK_INTERVAL_HOURS),
    "last_check_at": "",
    "last_check_summary": "",
}


@contextmanager
def get_conn():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        for key, value in _DEFAULT_SETTINGS.items():
            cur = conn.execute("SELECT 1 FROM settings WHERE key = ?", (key,))
            if cur.fetchone() is None:
                conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (key, value))


def get_setting(key, default=None):
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key, value):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )


def list_patterns():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM patterns ORDER BY pattern COLLATE NOCASE").fetchall()
        return [dict(r) for r in rows]


def add_pattern(pattern, days, is_regex=False, enabled=True):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO patterns (pattern, days, is_regex, enabled, created_at) VALUES (?, ?, ?, ?, ?)",
            (pattern.strip(), int(days), int(bool(is_regex)), int(bool(enabled)), int(time.time())),
        )


def update_pattern(pattern_id, pattern, days, is_regex, enabled):
    with get_conn() as conn:
        conn.execute(
            "UPDATE patterns SET pattern = ?, days = ?, is_regex = ?, enabled = ? WHERE id = ?",
            (pattern.strip(), int(days), int(bool(is_regex)), int(bool(enabled)), pattern_id),
        )


def delete_pattern(pattern_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM patterns WHERE id = ?", (pattern_id,))


def log_deletion(eventname, filename, pattern, recordingtime, success, message):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO deletion_log "
            "(eventname, filename, pattern, recordingtime, deleted_at, success, message) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (eventname, filename, pattern, recordingtime, int(time.time()), int(bool(success)), message),
        )


def list_deletion_log(limit=200):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM deletion_log ORDER BY deleted_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
