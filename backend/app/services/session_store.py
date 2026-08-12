"""User session store: maps a Telegram user to their current booking context.

v1 is in-memory (a process-local dict). It is deliberately hidden behind a small
class with async methods so it can be swapped for Redis / a database later
WITHOUT touching callers — the interface stays the same. This is why bookings
are keyed per ``telegram_user_id`` instead of one global booking.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


@dataclass
class UserSession:
    """Per-user context. Extend freely (e.g. history) without changing callers."""

    telegram_user_id: int
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    language_code: str | None = None
    # The user's current active booking code (authoritative code is always the
    # LATEST one SportyBet issued for this user).
    current_booking_code: str | None = None
    # Free-form profile snapshot from the last verified login.
    profile: dict[str, Any] = field(default_factory=dict)


class SessionStore:
    """Async, lock-guarded in-memory session store.

    The async signatures are intentional: a future Redis/DB backend will be
    async, so callers already ``await`` today and won't need to change.
    """

    def __init__(self) -> None:
        self._sessions: dict[int, UserSession] = {}
        self._lock = asyncio.Lock()

    async def upsert_from_login(
        self,
        telegram_user_id: int,
        *,
        username: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        language_code: str | None = None,
        profile: dict[str, Any] | None = None,
    ) -> UserSession:
        """Create or refresh a session on a verified login (preserves booking)."""
        async with self._lock:
            session = self._sessions.get(telegram_user_id)
            if session is None:
                session = UserSession(telegram_user_id=telegram_user_id)
                self._sessions[telegram_user_id] = session
            session.username = username
            session.first_name = first_name
            session.last_name = last_name
            session.language_code = language_code
            if profile is not None:
                session.profile = profile
            return session

    async def get(self, telegram_user_id: int) -> UserSession | None:
        async with self._lock:
            return self._sessions.get(telegram_user_id)

    async def set_current_booking(
        self, telegram_user_id: int, booking_code: str | None
    ) -> UserSession:
        """Record the user's latest booking code (the new authoritative code)."""
        async with self._lock:
            session = self._sessions.get(telegram_user_id)
            if session is None:
                session = UserSession(telegram_user_id=telegram_user_id)
                self._sessions[telegram_user_id] = session
            session.current_booking_code = booking_code
            return session

    async def clear(self, telegram_user_id: int) -> None:
        async with self._lock:
            self._sessions.pop(telegram_user_id, None)

    async def count(self) -> int:
        async with self._lock:
            return len(self._sessions)


# Process-wide singleton for v1. Swap the construction here (or inject) when a
# persistent backend arrives.
session_store = SessionStore()
