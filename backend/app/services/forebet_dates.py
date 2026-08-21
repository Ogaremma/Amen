from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from urllib.parse import urljoin
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.config.settings import get_settings

try:
    _LAGOS = ZoneInfo("Africa/Lagos")
except ZoneInfoNotFoundError:
    _LAGOS = timezone(timedelta(hours=1), name="Africa/Lagos")


def forebet_prediction_url(target_date: date, *, base_url: str | None = None) -> str:
    """Return Forebet's verified date-specific 1X2 prediction route."""
    base = (base_url or get_settings().forebet_base_url).rstrip("/") + "/"
    return urljoin(base, f"en/football-predictions/predictions-1x2/{target_date.isoformat()}")


def future_prediction_dates(*, now: datetime | None = None, count: int = 3) -> list[date]:
    if count < 1:
        return []
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    today_lagos = current.astimezone(_LAGOS).date()
    return [today_lagos + timedelta(days=offset) for offset in range(1, count + 1)]


def future_prediction_urls(*, now: datetime | None = None, count: int = 3, base_url: str | None = None) -> list[str]:
    return [forebet_prediction_url(day, base_url=base_url) for day in future_prediction_dates(now=now, count=count)]
