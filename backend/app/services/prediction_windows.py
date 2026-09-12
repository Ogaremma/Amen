from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.schemas.prediction import PredictionWindow


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def today_tomorrow_window(now: datetime) -> PredictionWindow:
    current = _as_utc(now)
    today_midnight = current.replace(hour=0, minute=0, second=0, microsecond=0)
    day_after_tomorrow_midnight = today_midnight + timedelta(days=2)
    start = max(current, today_midnight)
    return PredictionWindow(start=start, end_exclusive=day_after_tomorrow_midnight)


def in_today_tomorrow_window(kickoff: datetime | None, now: datetime) -> bool:
    if kickoff is None:
        return False
    kickoff_utc = _as_utc(kickoff)
    window = today_tomorrow_window(now)
    return window.start <= kickoff_utc < window.end_exclusive
