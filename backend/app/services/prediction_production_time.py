from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.schemas.prediction import PredictionWindow
from app.services.prediction_daily import DailyPredictionWindow


PRODUCTION_PREDICTION_TIMEZONE_NAME = 'Africa/Lagos'
try:
    PRODUCTION_PREDICTION_TIMEZONE = ZoneInfo(PRODUCTION_PREDICTION_TIMEZONE_NAME)
except ZoneInfoNotFoundError:
    from datetime import timedelta

    PRODUCTION_PREDICTION_TIMEZONE = timezone(timedelta(hours=1), name='Africa/Lagos')


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def production_prediction_window(now: datetime) -> PredictionWindow:
    """Build the production TODAY/TOMORROW window in Africa/Lagos."""

    current = _utc(now)
    local_current = current.astimezone(PRODUCTION_PREDICTION_TIMEZONE)
    local_midnight = local_current.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    start = max(current, local_midnight.astimezone(timezone.utc))
    end = (local_midnight + timedelta(days=2)).astimezone(timezone.utc)
    return PredictionWindow(start=start, end_exclusive=end)


def production_daily_prediction_window(now: datetime) -> DailyPredictionWindow:
    current = _utc(now)
    local_current = current.astimezone(PRODUCTION_PREDICTION_TIMEZONE)
    today = local_current.date()
    return DailyPredictionWindow(
        timezone_name=PRODUCTION_PREDICTION_TIMEZONE_NAME,
        today=today,
        tomorrow=today + timedelta(days=1),
        window=production_prediction_window(current),
    )
