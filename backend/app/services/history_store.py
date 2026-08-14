from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from app.config.settings import get_settings

HISTORY_LIMIT = 50


class HistoryStore:
    def __init__(self, path: str | None = None) -> None:
        self.path = path or get_settings().history_database_path
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        db = self._connect()
        try:
            db.execute("""CREATE TABLE IF NOT EXISTS booking_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_user_id INTEGER NOT NULL,
                booking_code TEXT NOT NULL,
                loaded_at TEXT NOT NULL,
                selection_count INTEGER,
                remaining_odds REAL,
                UNIQUE (telegram_user_id, booking_code))""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_history_user_loaded ON booking_history (telegram_user_id, loaded_at DESC)")
            db.commit()
        finally:
            db.close()

    def upsert(self, user_id: int, booking_code: str, selection_count: int, remaining_odds: float) -> None:
        loaded_at = datetime.now(timezone.utc).isoformat()
        db = self._connect()
        try:
            db.execute("""INSERT INTO booking_history
                (telegram_user_id, booking_code, loaded_at, selection_count, remaining_odds)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(telegram_user_id, booking_code) DO UPDATE SET
                loaded_at=excluded.loaded_at, selection_count=excluded.selection_count,
                remaining_odds=excluded.remaining_odds""",
                (user_id, booking_code.upper(), loaded_at, selection_count, remaining_odds))
            db.execute("""DELETE FROM booking_history WHERE telegram_user_id=? AND id NOT IN (
                SELECT id FROM booking_history WHERE telegram_user_id=?
                ORDER BY loaded_at DESC, id DESC LIMIT ?)""", (user_id, user_id, HISTORY_LIMIT))
            db.commit()
        finally:
            db.close()

    def list(self, user_id: int) -> list[dict]:
        db = self._connect()
        try:
            rows = db.execute("""SELECT id, booking_code, loaded_at, selection_count, remaining_odds
                FROM booking_history WHERE telegram_user_id=?
                ORDER BY loaded_at DESC, id DESC LIMIT ?""", (user_id, HISTORY_LIMIT)).fetchall()
            return [dict(row) for row in rows]
        finally:
            db.close()


history_store = HistoryStore()
