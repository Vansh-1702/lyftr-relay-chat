"""Single-file SQLite message history, with an atomic write per completed turn."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


class Storage:
    def __init__(self, url: str):
        prefix = "sqlite:///"
        if not url.startswith(prefix) or not url[len(prefix):] or url[len(prefix):] == ":memory:":
            raise ValueError("DATABASE_URL must point to a SQLite file")
        self.path = Path(url[len(prefix):])

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    system_prompt TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES sessions(id),
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    metadata TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_messages_session ON messages(session_id, id);
            """)

    def ping(self):
        with self.connection() as conn:
            conn.execute("SELECT id FROM messages LIMIT 1").fetchone()

    def read(self, session_id: str):
        with self.connection() as conn:
            rows = conn.execute("""
                SELECT s.system_prompt, m.role, m.content, m.metadata, m.created_at
                  FROM sessions AS s JOIN messages AS m ON s.id = m.session_id
                 WHERE s.id = ? ORDER BY m.id
            """, (session_id,)).fetchall()
        return (
            rows[0]["system_prompt"] if rows else None,
            [
                {
                    "role": row["role"], "content": row["content"], "created_at": row["created_at"],
                    **({"metadata": json.loads(row["metadata"])} if row["metadata"] is not None else {}),
                }
                for row in rows
            ],
        )

    def save_turn(self, session_id: str, prompt: str, message: str, reply: str, metadata: dict):
        now = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        with self.connection() as conn:
            conn.execute("""
                INSERT INTO sessions (id, system_prompt) VALUES (?, ?)
                ON CONFLICT(id) DO UPDATE SET system_prompt = excluded.system_prompt
            """, (session_id, prompt))
            conn.executemany("""
                INSERT INTO messages (session_id, role, content, metadata, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (
                (session_id, "user", message, None, now),
                (session_id, "assistant", reply, json.dumps(metadata), now),
            ))
