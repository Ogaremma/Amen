from datetime import datetime, timezone

from app.services.forebet_dates import forebet_prediction_url, future_prediction_dates, future_prediction_urls


def test_verified_date_route():
    assert forebet_prediction_url(datetime(2026, 8, 22, tzinfo=timezone.utc).date()) == "https://www.forebet.com/en/football-predictions/predictions-1x2/2026-08-22"


def test_future_dates_use_lagos_calendar_and_exclude_today():
    now = datetime(2026, 8, 20, 23, 30, tzinfo=timezone.utc)
    assert future_prediction_dates(now=now, count=3) == [datetime(2026, 8, 21).date(), datetime(2026, 8, 22).date(), datetime(2026, 8, 23).date()]


def test_urls_are_dynamic_and_ordered():
    now = datetime(2026, 8, 21, 12, tzinfo=timezone.utc)
    assert future_prediction_urls(now=now, count=3) == [
        "https://www.forebet.com/en/football-predictions/predictions-1x2/2026-08-22",
        "https://www.forebet.com/en/football-predictions/predictions-1x2/2026-08-23",
        "https://www.forebet.com/en/football-predictions/predictions-1x2/2026-08-24",
    ]
