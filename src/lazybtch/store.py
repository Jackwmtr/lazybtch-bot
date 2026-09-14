"""SQLite storage: users, transcript cache (TTL), events.

Single-file WAL database. Plaintext Groq keys never touch this file —
only Fernet blobs. Audio bytes are never stored, only transcripts.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  user_id     INTEGER PRIMARY KEY,
  username    TEXT,
  groq_key    TEXT,                     -- Fernet blob, NEVER plaintext
  lang        TEXT DEFAULT 'auto',
  created_at  TEXT DEFAULT (datetime('now')),
  updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS transcripts (
  content_hash TEXT PRIMARY KEY,        -- sha256(audio bytes)
  user_id      INTEGER,
  lang         TEXT,
  duration_s   INTEGER,
  text         TEXT,
  created_at   TEXT DEFAULT (datetime('now')),
  hits         INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER, kind TEXT,
  detail TEXT,                          -- no audio, no keys, no transcript text
  ts TEXT DEFAULT (datetime('now'))
);
"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class Store:
    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()
        os.chmod(self._path, 0o600)

    # ---------- users ----------

    def get_user(self, user_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
        return dict(row) if row else None

    def ensure_user(self, user_id: int, username: str | None = None) -> None:
        now = _utcnow()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO users (user_id, username, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                  username = COALESCE(excluded.username, users.username),
                  updated_at = excluded.updated_at
                """,
                (user_id, username, now, now),
            )
            self._conn.commit()

    def set_groq_key(self, user_id: int, key_blob: bytes | None) -> None:
        """Store an encrypted key blob, or delete it (None)."""
        self.ensure_user(user_id)
        with self._lock:
            self._conn.execute(
                "UPDATE users SET groq_key = ?, updated_at = ? WHERE user_id = ?",
                (key_blob, _utcnow(), user_id),
            )
            self._conn.commit()

    def set_lang(self, user_id: int, lang: str) -> None:
        self.ensure_user(user_id)
        with self._lock:
            self._conn.execute(
                "UPDATE users SET lang = ?, updated_at = ? WHERE user_id = ?",
                (lang, _utcnow(), user_id),
            )
            self._conn.commit()

    # ---------- transcript cache ----------

    def cache_get(self, content_hash: str) -> dict | None:
        with self._lock:
            hit = self._conn.execute(
                "SELECT 1 FROM transcripts WHERE content_hash = ?", (content_hash,)
            ).fetchone()
            if hit:
                self._conn.execute(
                    "UPDATE transcripts SET hits = hits + 1 WHERE content_hash = ?",
                    (content_hash,),
                )
                self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM transcripts WHERE content_hash = ?", (content_hash,)
            ).fetchone()
        return dict(row) if row else None

    def cache_set(
        self,
        content_hash: str,
        user_id: int,
        lang: str,
        duration_s: int | None,
        text: str,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO transcripts (content_hash, user_id, lang, duration_s, text)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(content_hash) DO NOTHING
                """,
                (content_hash, user_id, lang, duration_s, text),
            )
            self._conn.commit()

    def cache_purge_older_than(self, days: int) -> int:
        """Drop cache rows older than `days` (TTL cleanup). Returns rows removed."""
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=days)
        ).strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM transcripts WHERE created_at < ?", (cutoff,)
            )
            self._conn.commit()
        return cur.rowcount

    # ---------- events / stats ----------

    def add_event(self, user_id: int | None, kind: str, detail: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO events (user_id, kind, detail) VALUES (?, ?, ?)",
                (user_id, kind, detail),
            )
            self._conn.commit()

    def user_stats(self, user_id: int) -> dict:
        """Counts per kind, total ok duration, recent error-like events."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT kind, COUNT(*) AS n FROM events "
                "WHERE user_id = ? GROUP BY kind",
                (user_id,),
            ).fetchall()
            ok_rows = self._conn.execute(
                "SELECT detail FROM events WHERE user_id = ? AND kind = 'ok'",
                (user_id,),
            ).fetchall()
            recent = self._conn.execute(
                "SELECT kind, detail, ts FROM events "
                "WHERE user_id = ? AND kind != 'ok' ORDER BY id DESC LIMIT 5",
                (user_id,),
            ).fetchall()
        counts = {r["kind"]: r["n"] for r in rows}
        # ok events store detail like "d=12" (duration seconds)
        total = 0
        for r in ok_rows:
            d = r["detail"] or ""
            if d.startswith("d="):
                try:
                    total += int(d[2:])
                except ValueError:
                    pass
        return {
            "counts": counts,
            "total_duration_s": int(total or 0),
            "recent_errors": [dict(r) for r in recent],
        }

    def close(self) -> None:
        with self._lock:
            self._conn.close()
