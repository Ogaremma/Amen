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
        "forebet": {"sources_attempted": len(urls), "sources_succeeded": 0, "matches_parsed": 0, "draw_matches": 0, "errors": []},
        "sportybet": {"events_retrieved": 0, "football_events": 0, "total_num": None, "error": None},
        "matching": {"matched_exact": 0, "matched_normalized": 0, "matched_fuzzy": 0, "unmatched": 0, "ambiguous": 0},
        "booking_candidates": {"dates_with_valid_candidates": 0, "total_valid_selections": 0, "validation_errors": 0},
        "selections": [],
        "database": database_diagnostics(),
        "worker_execution": {"last_started": forebet_draw_worker.last_started, "last_completed": forebet_draw_worker.last_completed, "last_failure": forebet_draw_worker.last_failure, "last_failure_stage": forebet_draw_worker.last_failure_stage},
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
    draws = get_draw_matches(matches)
    report["forebet"]["draw_matches"] = len(draws)
    selected_by_date: dict[date, list] = {}
    for match in draws:
        if match.kickoff is None:
            continue
        day = match.kickoff.date() if isinstance(match.kickoff, datetime) else match.kickoff
        selected_by_date.setdefault(day, []).append(match)
    selected = [match for day in sorted(selected_by_date) for match in rank_draw_matches(selected_by_date[day], settings.forebet_draw_selection_limit)]
    report["forebet"]["selected_draw_matches"] = len(selected)
    report["forebet"]["selection_limit_per_day"] = settings.forebet_draw_selection_limit
    if not selected:
        return report
    try:
        upcoming = await get_upcoming_football_events()
        events = upcoming.events
        report["sportybet"]["total_num"] = upcoming.total_num
        report["sportybet"]["events_retrieved"] = len(events)
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
            report["selections"][-1]["booking_candidate"] = True
        except Exception:
            report["booking_candidates"]["validation_errors"] += 1
    report["booking_candidates"]["dates_with_valid_candidates"] = len(valid_dates)
    return report
