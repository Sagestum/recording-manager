import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from .config import DB_PATH, DEFAULT_CHECK_INTERVAL_HOURS, DEFAULT_PLAYLIST_URL

SCHEMA = """
CREATE TABLE IF NOT EXISTS patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern TEXT NOT NULL,
    days INTEGER NOT NULL,
    is_regex INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    priority INTEGER NOT NULL DEFAULT 0,
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

CREATE TABLE IF NOT EXISTS channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service_ref TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    encrypted INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS conflict_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    servicename TEXT NOT NULL,
    begin INTEGER,
    end_ts INTEGER,
    priority INTEGER,
    reason TEXT,
    success INTEGER NOT NULL,
    message TEXT,
    created_at INTEGER NOT NULL
);
"""

_DEFAULT_SETTINGS = {
    "check_interval_hours": str(DEFAULT_CHECK_INTERVAL_HOURS),
    "default_retention_days": "0",
    "last_check_at": "",
    "last_check_summary": "",
    "tuner_count": "2",
    "default_priority": "0",
    "playlist_url": DEFAULT_PLAYLIST_URL,
    "conflict_auto_resolve": "0",
    "conflict_check_interval_minutes": "30",
    "last_conflict_check_at": "",
    "last_conflict_check_summary": "",
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


def _migrate(conn):
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(patterns)").fetchall()]
    if "priority" not in cols:
        conn.execute("ALTER TABLE patterns ADD COLUMN priority INTEGER NOT NULL DEFAULT 0")


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
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


def add_pattern(pattern, days, is_regex=False, enabled=True, priority=0):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO patterns (pattern, days, is_regex, enabled, priority, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                pattern.strip(),
                int(days),
                int(bool(is_regex)),
                int(bool(enabled)),
                int(priority),
                int(time.time()),
            ),
        )


def update_pattern(pattern_id, pattern, days, is_regex, enabled, priority=0):
    with get_conn() as conn:
        conn.execute(
            "UPDATE patterns SET pattern = ?, days = ?, is_regex = ?, enabled = ?, priority = ? "
            "WHERE id = ?",
            (
                pattern.strip(),
                int(days),
                int(bool(is_regex)),
                int(bool(enabled)),
                int(priority),
                pattern_id,
            ),
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


def list_channels():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM channels ORDER BY name COLLATE NOCASE").fetchall()
        return [dict(r) for r in rows]


def upsert_channels(channels):
    """channels: Liste von {service_ref, name}. Bestehende 'encrypted'-Flags bleiben erhalten."""
    now = int(time.time())
    with get_conn() as conn:
        for ch in channels:
            conn.execute(
                "INSERT INTO channels (service_ref, name, encrypted, updated_at) VALUES (?, ?, 0, ?) "
                "ON CONFLICT(service_ref) DO UPDATE SET name = excluded.name, updated_at = excluded.updated_at",
                (ch["service_ref"], ch["name"], now),
            )


def set_channels_encrypted(encrypted_ids):
    """encrypted_ids: Menge/Liste von channel-IDs, die als verschluesselt markiert werden. Alle anderen werden zurueckgesetzt."""
    ids = {int(i) for i in encrypted_ids}
    with get_conn() as conn:
        conn.execute("UPDATE channels SET encrypted = 0")
        if ids:
            conn.executemany(
                "UPDATE channels SET encrypted = 1 WHERE id = ?", [(i,) for i in ids]
            )


def log_conflict_action(name, servicename, begin, end, priority, reason, success, message):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO conflict_log "
            "(name, servicename, begin, end_ts, priority, reason, success, message, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (name, servicename, begin, end, priority, reason, int(bool(success)), message, int(time.time())),
        )


def list_conflict_log(limit=200):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM conflict_log ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
