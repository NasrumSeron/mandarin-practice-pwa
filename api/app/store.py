"""store.py — SQLite progress, keyed (email, item_id, mode). Leitner is computed here."""
from __future__ import annotations

import sqlite3
import threading
from datetime import date, datetime, timedelta, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS progress (
  email TEXT NOT NULL, item_id TEXT NOT NULL, mode TEXT NOT NULL,
  box INTEGER NOT NULL, due TEXT NOT NULL,
  correct_count INTEGER NOT NULL DEFAULT 0, wrong_count INTEGER NOT NULL DEFAULT 0,
  last_seen TEXT NOT NULL,
  PRIMARY KEY (email, item_id, mode));
"""
MAX_BOX = 5


def next_state(box: int, correct: bool, days: list[int], today: date) -> tuple[int, str]:
    """Leitner step: correct promotes (cap 5), wrong resets to 1. -> (box, due)."""
    box = min(box + 1, MAX_BOX) if correct else 1
    gap = days[min(box, len(days)) - 1]
    return box, (today + timedelta(days=gap)).isoformat()


class Store:
    def __init__(self, path: str = ":memory:"):
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=10)
        self.db.executescript(SCHEMA)
        self.lock = threading.Lock()

    def list(self, email: str) -> list[dict]:
        with self.lock:
            rows = self.db.execute(
                "SELECT item_id, mode, box, due, correct_count, wrong_count, last_seen "
                "FROM progress WHERE email = ? ORDER BY due, item_id, mode", (email,)).fetchall()
        keys = ("item_id", "mode", "box", "due", "correct_count", "wrong_count", "last_seen")
        return [dict(zip(keys, r)) for r in rows]

    def record(self, email: str, item_id: str, mode: str, correct: bool,
               days: list[int], now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        with self.lock:
            row = self.db.execute(
                "SELECT box, correct_count, wrong_count FROM progress "
                "WHERE email = ? AND item_id = ? AND mode = ?", (email, item_id, mode)).fetchone()
            box0, c, w = row if row else (0, 0, 0)
            box, due = next_state(box0, correct, days, now.date())
            c, w = c + correct, w + (not correct)
            self.db.execute(
                "INSERT INTO progress (email, item_id, mode, box, due, correct_count, wrong_count, last_seen) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (email, item_id, mode) DO UPDATE SET box = excluded.box, "
                "due = excluded.due, correct_count = excluded.correct_count, "
                "wrong_count = excluded.wrong_count, last_seen = excluded.last_seen",
                (email, item_id, mode, box, due, c, w, now.isoformat(timespec="seconds")))
            self.db.commit()
        return {"item_id": item_id, "mode": mode, "box": box, "due": due,
                "correct_count": c, "wrong_count": w, "last_seen": now.isoformat(timespec="seconds")}
