"""SQLite storage. One table; extracted fields stored as JSON."""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

DB_PATH = os.getenv("OPS_DB", "ops.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    text        TEXT NOT NULL,
    extracted   TEXT NOT NULL,
    method      TEXT NOT NULL,
    status      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS status_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id  INTEGER NOT NULL REFERENCES requests(id),
    old_status  TEXT,
    new_status  TEXT NOT NULL,
    changed_at  TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect(path: str | None = None):
    conn = sqlite3.connect(path or DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init(path: str | None = None) -> None:
    with connect(path) as conn:
        conn.executescript(SCHEMA)


def insert(conn, text: str, extracted: dict, method: str, status: str) -> int:
    now = _now()
    cur = conn.execute(
        "INSERT INTO requests (text, extracted, method, status, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (text, json.dumps(extracted), method, status, now, now),
    )
    conn.execute(
        "INSERT INTO status_history (request_id, old_status, new_status, changed_at) VALUES (?, NULL, ?, ?)",
        (cur.lastrowid, status, now),
    )
    return cur.lastrowid


def get(conn, request_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM requests WHERE id = ?", (request_id,)).fetchone()


def list_all(conn, status: str | None = None) -> list[sqlite3.Row]:
    if status:
        return conn.execute("SELECT * FROM requests WHERE status = ? ORDER BY id DESC", (status,)).fetchall()
    return conn.execute("SELECT * FROM requests ORDER BY id DESC").fetchall()


def set_status(conn, request_id: int, old: str, new: str) -> None:
    now = _now()
    conn.execute("UPDATE requests SET status = ?, updated_at = ? WHERE id = ?", (new, now, request_id))
    conn.execute(
        "INSERT INTO status_history (request_id, old_status, new_status, changed_at) VALUES (?, ?, ?, ?)",
        (request_id, old, new, now),
    )
