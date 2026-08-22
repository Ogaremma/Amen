from collections import Counter
from datetime import date, datetime
from collections import defaultdict
from app.config.settings import get_settings
from app.schemas.forebet import FixtureMatchStatus
from app.schemas.forebet_ingestion import ForebetAcquisitionSnapshotRequest
from app.services.fixture_matching import match_forebet_fixtures
from app.services.sportybet import _draw_selection, create_draw_booking, get_upcoming_football_events
from app.schemas.forebet_draw_window import DrawWindowMatch
from app.services.forebet_draw_store import forebet_draw_store

async def process_snapshot(request: ForebetAcquisitionSnapshotRequest, *, execute_booking: bool = False) -> dict:
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
    report = {"dry_run": not execute_booking, "forebet_pages_acquired": len(request.snapshots), "forebet_matches_parsed": len(matches), "forebet_draw_predictions": len(draws), "sportybet_events_retrieved": len(upcoming.events), "matched": counts[FixtureMatchStatus.MATCHED_EXACT] + counts[FixtureMatchStatus.MATCHED_NORMALIZED] + counts[FixtureMatchStatus.MATCHED_FUZZY], "unmatched": counts[FixtureMatchStatus.UNMATCHED], "ambiguous": counts[FixtureMatchStatus.AMBIGUOUS], "booking_candidates": sum(1 for row in rows if row["booking_eligible"]), "rows": rows, "bookings": []}
    for snapshot in request.snapshots:
        day_rows = [row for row in rows if row["prediction_date"] == snapshot.prediction_date]
        forebet_draws = [draw for draw in draws if (draw.kickoff.date() if isinstance(draw.kickoff, datetime) else draw.kickoff) == snapshot.prediction_date]
        day_rows.sort(key=lambda row: row["draw_probability"] if row["draw_probability"] is not None else -1, reverse=True)
        forebet_draw_store.save_prebooking(snapshot.prediction_date, day_rows, {"source_url": snapshot.source_url, "forebet_draw_count": len(forebet_draws), "booking_candidates": sum(row["booking_eligible"] for row in day_rows)})
    if execute_booking:
        grouped = defaultdict(list)
        for result in results:
            event = result.sportybet_event
            if result.status not in {FixtureMatchStatus.MATCHED_EXACT, FixtureMatchStatus.MATCHED_NORMALIZED, FixtureMatchStatus.MATCHED_FUZZY} or event is None:
                continue
            try:
                _draw_selection(event)
            except Exception:
                continue
            kickoff = result.forebet_match.kickoff
            if kickoff is not None:
                grouped[kickoff.date() if isinstance(kickoff, datetime) else kickoff].append(result)
        for prediction_date, items in sorted(grouped.items()):
            deduped = {}
            for item in items:
                deduped.setdefault(item.sportybet_event.event_id, item)
            existing = {day.prediction_date: day for day in forebet_draw_store.list_active()}.get(prediction_date)
            window_matches = [DrawWindowMatch(event_id=i.sportybet_event.event_id, home_team=i.sportybet_event.home_team, away_team=i.sportybet_event.away_team, kickoff=i.sportybet_event.kickoff, match_status=i.sportybet_event.match_status, market_id="1", outcome_id="2", product_id=i.sportybet_event.product_id, sport_id=i.sportybet_event.sport_id, specifier=i.sportybet_event.specifier) for i in deduped.values()]
            identity = sorted((m.event_id, m.market_id, m.outcome_id, m.product_id, m.sport_id, m.specifier or "") for m in window_matches)
            old_identity = sorted((m.event_id, m.market_id, m.outcome_id, m.product_id, m.sport_id, m.specifier or "") for m in existing.matches) if existing else None
            if identity == old_identity:
                report["bookings"].append({"prediction_date": prediction_date, "reused": True, "booking_code": existing.booking_code})
                continue
            booking = await create_draw_booking(list(deduped.values()))
            forebet_draw_store.promote(prediction_date, booking.booking_code, window_matches, [s.source_url for s in request.snapshots], [])
            report["bookings"].append({"prediction_date": prediction_date, "reused": False, "booking_code": booking.booking_code, "selection_count": len(window_matches)})
    return report


async def dry_run_snapshot(request: ForebetAcquisitionSnapshotRequest) -> dict:
    return await process_snapshot(request, execute_booking=False)


async def execute_snapshot(request: ForebetAcquisitionSnapshotRequest) -> dict:
    return await process_snapshot(request, execute_booking=True)
