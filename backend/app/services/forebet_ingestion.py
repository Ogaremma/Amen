from collections import Counter
from datetime import date, datetime, timedelta, timezone
from collections import defaultdict
import json
from pathlib import Path
import re
from app.config.settings import get_settings
from app.schemas.forebet import FixtureMatchStatus
from app.schemas.forebet_ingestion import ForebetAcquisitionSnapshotRequest
from app.services.fixture_matching import match_forebet_fixtures
from app.services.sportybet import (
    _draw_selection,
    create_draw_booking,
    get_upcoming_football_events,
    parse_upcoming_events_page,
)
from app.schemas.forebet_draw_window import DrawWindowMatch
from app.services.forebet_draw_store import forebet_draw_store
from app.services.forebet import parse_forebet_html
from app.services.forebet_dates import future_prediction_dates
from app.services.forebet_draw_engine import ForebetDrawEngine, forebet_draw_engine

_LAGOS = timezone(timedelta(hours=1), name="Africa/Lagos")


def _kickoff_lagos_date(value):
    if isinstance(value, datetime):
        aware = (
            value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        )
        return aware.astimezone(_LAGOS).date()
    return value


def _local_sportybet_events(snapshot_dir: Path | None = None):
    directory = snapshot_dir or Path(__file__).resolve().parents[3] / "snapshots"
    files = []
    for path in directory.glob("sportybet-page-*.json"):
        match = re.fullmatch(r"sportybet-page-(\d+)\.json", path.name)
        if match:
            files.append((int(match.group(1)), path))
    files.sort()
    if not files:
        raise ValueError(f"no local SportyBet snapshots found in {directory}")
    events = []
    total_num = 0
    for _, path in files:
        parsed = parse_upcoming_events_page(
            json.loads(path.read_text(encoding="utf-8"))
        )
        total_num = max(total_num, parsed.total_num)
        events.extend(parsed.events)
    return type(
        "SnapshotEvents",
        (),
        {"events": events, "total_num": total_num, "pages_fetched": len(files)},
    )()


async def process_snapshot(
    request: ForebetAcquisitionSnapshotRequest,
    *,
    execute_booking: bool = False,
    engine: ForebetDrawEngine | None = None,
) -> dict:
    settings = get_settings()
    engine = engine or ForebetDrawEngine(forebet_draw_store)
    rolling_dates = future_prediction_dates()
    allowed_dates = set(rolling_dates)
    trusted_mode = any(snapshot.raw_html is not None for snapshot in request.snapshots)
    if trusted_mode and execute_booking:
        raise ValueError(
            "trusted snapshot processing is paper-only; real booking is disabled"
        )
    supplied_dates = [snapshot.prediction_date for snapshot in request.snapshots]
    if trusted_mode and (
        len(request.snapshots) != 3 or supplied_dates != rolling_dates
    ):
        raise ValueError(
            f"snapshots must contain exactly the current rolling dates in order: {', '.join(map(str, rolling_dates))}"
        )
    for snapshot in request.snapshots:
        if trusted_mode and snapshot.raw_html is None:
            raise ValueError(
                f"trusted snapshot raw_html is required for {snapshot.prediction_date}"
            )
        url_match = re.search(r"/(\d{4}-\d{2}-\d{2})(?:[/?#]|$)", snapshot.source_url)
        if (
            not url_match
            or date.fromisoformat(url_match.group(1)) != snapshot.prediction_date
        ):
            raise ValueError(
                f"snapshot source_url date does not match prediction_date: {snapshot.prediction_date}"
            )
    normalized = []
    for snapshot in request.snapshots:
        if snapshot.raw_html is not None:
            lowered = snapshot.raw_html.lower()
            if "<html" not in lowered or "schema" not in lowered:
                raise ValueError(
                    f"PARSER_FAILURE: invalid Forebet HTML for {snapshot.prediction_date}"
                )
            parsed = parse_forebet_html(snapshot.raw_html, snapshot.source_url)
            dated = [m for m in parsed if m.kickoff is not None]
            normalized.append(
                snapshot.model_copy(
                    update={
                        "matches": [
                            m
                            for m in dated
                            if _kickoff_lagos_date(m.kickoff)
                            == snapshot.prediction_date
                        ]
                    }
                )
            )
            forebet_draw_store.save_raw_snapshot(
                snapshot.prediction_date,
                snapshot.source_url,
                snapshot.raw_html,
                source=snapshot.source,
            )
        else:
            normalized.append(snapshot)
    matches = [m for snapshot in normalized for m in snapshot.matches]
    draws = [m for m in matches if m.predicted_result.value == "DRAW"]
    if request.sportybet_events is not None:
        target_dates = allowed_dates
        events = [
            event
            for event in request.sportybet_events
            if event.sport_id == settings.sportybet_football_sport_id
            and event.kickoff.date() in target_dates
        ]
        upcoming = type("SnapshotEvents", (), {"events": events})()
    elif request.use_local_sportybet_snapshots:
        local = _local_sportybet_events()
        events = [
            event
            for event in local.events
            if event.sport_id == settings.sportybet_football_sport_id
            and event.kickoff.date() in allowed_dates
        ]
        upcoming = type(
            "SnapshotEvents",
            (),
            {
                "events": events,
                "total_num": local.total_num,
                "pages_fetched": local.pages_fetched,
            },
        )()
    else:
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
        rows.append(
            {
                "prediction_date": (
                    result.forebet_match.kickoff.date()
                    if isinstance(result.forebet_match.kickoff, datetime)
                    else result.forebet_match.kickoff
                ),
                "home_team": result.forebet_match.home_team,
                "away_team": result.forebet_match.away_team,
                "draw_probability": result.forebet_match.probabilities.draw
                if result.forebet_match.probabilities
                else None,
                "status": result.status.value,
                "sportybet_event_id": event.event_id if event else None,
                "sportybet_kickoff": event.kickoff if event else None,
                "booking_eligible": eligible,
                "reason": reason,
            }
        )
    counts = Counter(r.status for r in results)
    report = {
        "dry_run": not execute_booking,
        "forebet_pages_acquired": len(request.snapshots),
        "forebet_matches_parsed": len(matches),
        "forebet_draw_predictions": len(draws),
        "sportybet_events_retrieved": len(upcoming.events),
        "matched": counts[FixtureMatchStatus.MATCHED_EXACT]
        + counts[FixtureMatchStatus.MATCHED_NORMALIZED]
        + counts[FixtureMatchStatus.MATCHED_FUZZY],
        "unmatched": counts[FixtureMatchStatus.UNMATCHED],
        "ambiguous": counts[FixtureMatchStatus.AMBIGUOUS],
        "booking_candidates": sum(1 for row in rows if row["booking_eligible"]),
        "rows": rows,
        "bookings": [],
    }
    for snapshot in normalized:
        day_rows = [
            row for row in rows if row["prediction_date"] == snapshot.prediction_date
        ]
        forebet_draws = [
            draw
            for draw in draws
            if (
                draw.kickoff.date()
                if isinstance(draw.kickoff, datetime)
                else draw.kickoff
            )
            == snapshot.prediction_date
        ]
        day_rows.sort(
            key=lambda row: (
                row["draw_probability"] if row["draw_probability"] is not None else -1
            ),
            reverse=True,
        )
        forebet_draw_store.save_prebooking(
            snapshot.prediction_date,
            day_rows,
            {
                "source_url": snapshot.source_url,
                "forebet_draw_count": len(forebet_draws),
                "booking_candidates": sum(row["booking_eligible"] for row in day_rows),
            },
        )
    if trusted_mode and not execute_booking:
        window = await engine.refresh_trusted(
            [snapshot.source_url for snapshot in normalized], matches, upcoming.events
        )
        report["paper_window"] = window.model_dump(mode="json")
    if execute_booking:
        grouped = defaultdict(list)
        for result in results:
            event = result.sportybet_event
            if (
                result.status
                not in {
                    FixtureMatchStatus.MATCHED_EXACT,
                    FixtureMatchStatus.MATCHED_NORMALIZED,
                    FixtureMatchStatus.MATCHED_FUZZY,
                }
                or event is None
            ):
                continue
            try:
                _draw_selection(event)
            except Exception:
                continue
            kickoff = result.forebet_match.kickoff
            if kickoff is not None:
                grouped[
                    kickoff.date() if isinstance(kickoff, datetime) else kickoff
                ].append(result)
        for prediction_date, items in sorted(grouped.items()):
            deduped = {}
            for item in items:
                deduped.setdefault(item.sportybet_event.event_id, item)
            existing = {
                day.prediction_date: day for day in forebet_draw_store.list_active()
            }.get(prediction_date)
            window_matches = [
                DrawWindowMatch(
                    event_id=i.sportybet_event.event_id,
                    home_team=i.sportybet_event.home_team,
                    away_team=i.sportybet_event.away_team,
                    kickoff=i.sportybet_event.kickoff,
                    match_status=i.sportybet_event.match_status,
                    market_id="1",
                    outcome_id="2",
                    product_id=i.sportybet_event.product_id,
                    sport_id=i.sportybet_event.sport_id,
                    specifier=i.sportybet_event.specifier,
                )
                for i in deduped.values()
            ]
            identity = sorted(
                (
                    m.event_id,
                    m.market_id,
                    m.outcome_id,
                    m.product_id,
                    m.sport_id,
                    m.specifier or "",
                )
                for m in window_matches
            )
            old_identity = (
                sorted(
                    (
                        m.event_id,
                        m.market_id,
                        m.outcome_id,
                        m.product_id,
                        m.sport_id,
                        m.specifier or "",
                    )
                    for m in existing.matches
                )
                if existing
                else None
            )
            if identity == old_identity:
                report["bookings"].append(
                    {
                        "prediction_date": prediction_date,
                        "reused": True,
                        "booking_code": existing.booking_code,
                    }
                )
                continue
            booking = await create_draw_booking(list(deduped.values()))
            forebet_draw_store.promote(
                prediction_date,
                booking.booking_code,
                window_matches,
                [s.source_url for s in request.snapshots],
                [],
            )
            report["bookings"].append(
                {
                    "prediction_date": prediction_date,
                    "reused": False,
                    "booking_code": booking.booking_code,
                    "selection_count": len(window_matches),
                }
            )
    return report


async def dry_run_snapshot(request: ForebetAcquisitionSnapshotRequest) -> dict:
    return await process_snapshot(request, execute_booking=False)


async def execute_snapshot(request: ForebetAcquisitionSnapshotRequest) -> dict:
    return await process_snapshot(request, execute_booking=True)
