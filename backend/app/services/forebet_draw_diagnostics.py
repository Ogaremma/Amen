from __future__ import annotations

from collections import Counter
from datetime import date, datetime

from sqlalchemy import func, inspect, select

from app.config.settings import get_settings
from app.schemas.forebet import FixtureMatchStatus
from app.services.fixture_matching import match_forebet_fixtures
from app.services.forebet import fetch_forebet_page, get_draw_matches, parse_forebet_html, rank_draw_matches
from app.services.forebet_dates import future_prediction_dates, future_prediction_urls
from app.services.forebet_draw_store import daily, forebet_draw_store, revisions
from app.services.forebet_draw_worker import forebet_draw_worker
from app.services.sportybet import _draw_selection, get_upcoming_football_events


def _failure(stage: str, exc: Exception) -> dict[str, str]:
    return {"stage": stage, "exception_type": type(exc).__name__, "message": str(exc)[:500]}


def database_diagnostics() -> dict:
    result = {"reachable": False, "daily_booking_table_available": False, "revision_table_available": False, "active_records": None, "error": None}
    try:
        inspector = inspect(forebet_draw_store.engine)
        result["reachable"] = True
        result["daily_booking_table_available"] = inspector.has_table(daily.name)
        result["revision_table_available"] = inspector.has_table(revisions.name)
        if result["daily_booking_table_available"]:
            with forebet_draw_store.engine.connect() as db:
                result["active_records"] = db.execute(select(func.count()).select_from(daily).where(daily.c.status == "active")).scalar_one()
    except Exception as exc:
        result["error"] = _failure("database", exc)
    return result


async def run_forebet_draw_diagnostics() -> dict:
    settings = get_settings()
    dates = future_prediction_dates()
    urls = future_prediction_urls()
    report = {
        "worker": {"configured": True, "running": forebet_draw_worker.running, "refresh_interval_seconds": settings.forebet_draw_refresh_interval_seconds},
        "dates": [{"prediction_date": day.isoformat(), "source_url": url} for day, url in zip(dates, urls)],
        "forebet": {"sources_attempted": len(urls), "sources_succeeded": 0, "matches_parsed": 0, "draw_matches": 0, "acquisition_status": "pending", "errors": []},
        "sportybet": {"events_retrieved": 0, "football_events": 0, "total_num": None, "pages_fetched": 0, "error": None},
        "matching": {"matched_exact": 0, "matched_normalized": 0, "matched_fuzzy": 0, "unmatched": 0, "ambiguous": 0},
        "booking_candidates": {"dates_with_valid_candidates": 0, "total_valid_selections": 0, "validation_errors": 0},
        "selections": [],
        "database": database_diagnostics(),
        "worker_execution": {"last_started": forebet_draw_worker.last_started, "last_completed": forebet_draw_worker.last_completed, "last_failure": forebet_draw_worker.last_failure, "last_failure_stage": forebet_draw_worker.last_failure_stage},
        "per_date": {day.isoformat(): {"fixtures": 0, "draws": 0, "exact": 0, "normalized": 0, "fuzzy": 0, "ambiguous": 0, "matched": 0, "valid": 0, "rejected": 0} for day in dates},
    }
    matches = []
    for url in urls:
        try:
            parsed = parse_forebet_html(await fetch_forebet_page(url), url)
            report["forebet"]["sources_succeeded"] += 1
            report["forebet"]["matches_parsed"] += len(parsed)
            matches.extend(parsed)
        except Exception as exc:
            report["forebet"]["errors"].append(_failure("forebet_acquisition", exc))
    target_dates = set(dates)
    matches = [match for match in matches if match.kickoff is not None and (match.kickoff.date() if isinstance(match.kickoff, datetime) else match.kickoff) in target_dates]
    for match in matches:
        day = match.kickoff.date() if isinstance(match.kickoff, datetime) else match.kickoff
        report["per_date"][day.isoformat()]["fixtures"] += 1
    if report["forebet"]["sources_succeeded"] == len(urls):
        report["forebet"]["acquisition_status"] = "success"
    elif any("403" in error["message"] or "captcha" in error["message"].lower() or "accessdenied" in error["exception_type"].lower() for error in report["forebet"]["errors"]):
        report["forebet"]["acquisition_status"] = "blocked_403_or_captcha"
    else:
        report["forebet"]["acquisition_status"] = "failed"
    draws = get_draw_matches(matches)
    report["forebet"]["draw_matches"] = len(draws)
    selected_by_date: dict[date, list] = {}
    for match in draws:
        if match.kickoff is None:
            continue
        day = match.kickoff.date() if isinstance(match.kickoff, datetime) else match.kickoff
        selected_by_date.setdefault(day, []).append(match)
        report["per_date"][day.isoformat()]["draws"] += 1
    selected = [match for day in sorted(selected_by_date) for match in rank_draw_matches(selected_by_date[day], limit=None)]
    report["forebet"]["selected_draw_matches"] = len(selected)
    report["forebet"]["selection_limit_per_day"] = None
    if not selected:
        return report
    try:
        upcoming = await get_upcoming_football_events()
        events = upcoming.events
        report["sportybet"]["total_num"] = upcoming.total_num
        report["sportybet"]["events_retrieved"] = len(events)
        report["sportybet"]["pages_fetched"] = upcoming.pages_fetched
        report["sportybet"]["football_events"] = sum(event.sport_id == settings.sportybet_football_sport_id for event in events)
    except Exception as exc:
        report["sportybet"]["error"] = _failure("sportybet_acquisition", exc)
        return report
    results = match_forebet_fixtures(selected, events)
    counts = Counter(result.status for result in results)
    report["matching"].update({
        "matched_exact": counts[FixtureMatchStatus.MATCHED_EXACT],
        "matched_normalized": counts[FixtureMatchStatus.MATCHED_NORMALIZED],
        "matched_fuzzy": counts[FixtureMatchStatus.MATCHED_FUZZY],
        "unmatched": counts[FixtureMatchStatus.UNMATCHED],
        "ambiguous": counts[FixtureMatchStatus.AMBIGUOUS],
    })
    valid_dates = set()
    for result in results:
        forebet = result.forebet_match
        event = result.sportybet_event
        result_day = forebet.kickoff.date() if isinstance(forebet.kickoff, datetime) else forebet.kickoff
        per_day = report["per_date"][result_day.isoformat()]
        if result.status == FixtureMatchStatus.MATCHED_EXACT: per_day["exact"] += 1
        elif result.status == FixtureMatchStatus.MATCHED_NORMALIZED: per_day["normalized"] += 1
        elif result.status == FixtureMatchStatus.MATCHED_FUZZY: per_day["fuzzy"] += 1
        elif result.status == FixtureMatchStatus.AMBIGUOUS: per_day["ambiguous"] += 1
        if result.status in {FixtureMatchStatus.MATCHED_EXACT, FixtureMatchStatus.MATCHED_NORMALIZED, FixtureMatchStatus.MATCHED_FUZZY}: per_day["matched"] += 1
        else: per_day["rejected"] += 1
        report["selections"].append({
            "prediction_date": (forebet.kickoff.date() if isinstance(forebet.kickoff, datetime) else forebet.kickoff).isoformat() if forebet.kickoff else None,
            "home_team": forebet.home_team,
            "away_team": forebet.away_team,
            "draw_probability": forebet.probabilities.draw if forebet.probabilities else None,
            "status": result.status.value,
            "matching_method": result.matching_method,
            "matching_confidence": result.matching_confidence,
            "sportybet_event_id": event.event_id if event else None,
            "sportybet_kickoff": event.kickoff.isoformat() if event else None,
            "draw_outcome_id": event.outcome_draw_id if event else None,
            "booking_candidate": False,
            "reason": result.reason,
        })
        if result.sportybet_event is None:
            continue
        try:
            _draw_selection(result.sportybet_event)
            kickoff = result.forebet_match.kickoff
            if kickoff is not None:
                valid_dates.add(kickoff.date() if isinstance(kickoff, datetime) else kickoff)
            report["booking_candidates"]["total_valid_selections"] += 1
            per_day["valid"] += 1
            report["selections"][-1]["booking_candidate"] = True
        except Exception:
            report["booking_candidates"]["validation_errors"] += 1
    report["booking_candidates"]["dates_with_valid_candidates"] = len(valid_dates)
    return report


def persisted_forebet_draw_diagnostics() -> dict:
    """Return the latest worker-produced state without contacting providers."""
    settings = get_settings()
    response = forebet_draw_worker.engine.get_active_window()
    days = response.days
    errors = [message for day in days for message in day.diagnostics if "FAILURE" in message or "error" in day.status]
    return {
        "worker": {"configured": True, "running": forebet_draw_worker.running, "refresh_interval_seconds": settings.forebet_draw_refresh_interval_seconds},
        "forebet": {"sources_attempted": len({url for day in days for url in day.source_urls}), "sources_succeeded": sum(day.acquisition.get("status") == "success" for day in days), "matches_parsed": sum(day.selection_count for day in days), "draw_matches": sum(day.selection_count for day in days), "acquisition_status": "failed" if errors else "success", "errors": errors},
        "database": database_diagnostics(),
        "worker_execution": {"last_started": forebet_draw_worker.last_started, "last_completed": forebet_draw_worker.last_completed, "last_failure": forebet_draw_worker.last_failure, "last_failure_stage": forebet_draw_worker.last_failure_stage},
        "per_date": {day.prediction_date.isoformat(): {"fixtures": day.selection_count, "draws": day.selection_count, "diagnostics": day.diagnostics, "acquisition": day.acquisition} for day in days},
        "selections": [], "sportybet": {"events_retrieved": 0, "football_events": 0, "total_num": None, "pages_fetched": 0, "error": None}, "matching": {}, "booking_candidates": {},
    }
