import os
import sqlite3
from datetime import datetime
from threading import Lock


class EventLog:
    def __init__(self, path):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._lock = Lock()
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    level TEXT NOT NULL,
                    source TEXT NOT NULL,
                    message TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def add(self, level, source, message):
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO events (created_at, level, source, message) VALUES (?, ?, ?, ?)",
                (datetime.now().astimezone().isoformat(timespec="seconds"), level, source, message),
            )
            conn.commit()

    def recent(self, limit=100):
        limit = max(1, min(int(limit), 250))
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT id, created_at, level, source, message FROM events ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def clear(self):
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM events")
            conn.commit()
