from collections import Counter
from datetime import datetime
from app.config.settings import get_settings
from app.schemas.forebet import FixtureMatchStatus
from app.schemas.forebet_ingestion import ForebetAcquisitionSnapshotRequest
from app.services.fixture_matching import match_forebet_fixtures
from app.services.sportybet import _draw_selection, get_upcoming_football_events
from app.services.forebet_draw_store import forebet_draw_store

async def dry_run_snapshot(request: ForebetAcquisitionSnapshotRequest) -> dict:
    settings = get_settings()
    matches = [m for snapshot in request.snapshots for m in snapshot.matches]
    draws = [m for m in matches if m.predicted_result.value == "DRAW"]
    upcoming = await get_upcoming_football_events()
    results = match_forebet_fixtures(draws, upcoming.events)
    rows = []
    for result in results:
        event = result.sportybet_event
        eligible = False
        reason = result.reason
        if event is not None:
            try:
                _draw_selection(event)
                eligible = True
                reason = None
            except Exception as exc:
                reason = str(exc)
        rows.append({"prediction_date": (result.forebet_match.kickoff.date() if isinstance(result.forebet_match.kickoff, datetime) else result.forebet_match.kickoff), "home_team": result.forebet_match.home_team, "away_team": result.forebet_match.away_team, "draw_probability": result.forebet_match.probabilities.draw if result.forebet_match.probabilities else None, "status": result.status.value, "sportybet_event_id": event.event_id if event else None, "sportybet_kickoff": event.kickoff if event else None, "booking_eligible": eligible, "reason": reason})
    counts = Counter(r.status for r in results)
    report = {"dry_run": True, "forebet_pages_acquired": len(request.snapshots), "forebet_matches_parsed": len(matches), "forebet_draw_predictions": len(draws), "sportybet_events_retrieved": len(upcoming.events), "matched": counts[FixtureMatchStatus.MATCHED_EXACT] + counts[FixtureMatchStatus.MATCHED_NORMALIZED] + counts[FixtureMatchStatus.MATCHED_FUZZY], "unmatched": counts[FixtureMatchStatus.UNMATCHED], "ambiguous": counts[FixtureMatchStatus.AMBIGUOUS], "booking_candidates": sum(1 for row in rows if row["booking_eligible"]), "rows": rows}
    for snapshot in request.snapshots:
        day_rows = [row for row in rows if row["prediction_date"] == snapshot.prediction_date]
        forebet_draws = [draw for draw in draws if (draw.kickoff.date() if isinstance(draw.kickoff, datetime) else draw.kickoff) == snapshot.prediction_date]
        day_rows.sort(key=lambda row: row["draw_probability"] if row["draw_probability"] is not None else -1, reverse=True)
        forebet_draw_store.save_prebooking(snapshot.prediction_date, day_rows, {"source_url": snapshot.source_url, "forebet_draw_count": len(forebet_draws), "booking_candidates": sum(row["booking_eligible"] for row in day_rows)})
    return report
