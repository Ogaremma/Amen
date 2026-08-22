from __future__ import annotations

from datetime import date, datetime
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Header

from app.config.settings import get_settings
from app.schemas.forebet import DrawBookingRequest, DrawBookingResponse, ForebetAnalyzeRequest, ForebetAnalyzeResponse, FixtureMatchDateGroup, SportyBetEvent
from app.services.fixture_matching import match_forebet_fixtures
from app.services.forebet import fetch_forebet_page, get_draw_matches, parse_forebet_html
from app.services.sportybet import create_draw_booking
from app.schemas.forebet_draw_window import DrawWindowRefreshRequest, DrawWindowResponse
from app.services.forebet_draw_engine import forebet_draw_engine
from app.services.forebet_draw_diagnostics import run_forebet_draw_diagnostics
from app.schemas.forebet_ingestion import ForebetAcquisitionSnapshotRequest
from app.services.forebet_ingestion import dry_run_snapshot, execute_snapshot

router = APIRouter(prefix="/api/v1/forebet", tags=["forebet"])

def _verify_ingestion_token(authorization: str | None) -> None:
    expected = get_settings().forebet_ingestion_token
    if not expected or authorization != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="Forebet ingestion authentication required")

@router.post("/acquisition-snapshots")
async def ingest_acquisition_snapshots(request: ForebetAcquisitionSnapshotRequest, authorization: str | None = Header(default=None)) -> dict:
    _verify_ingestion_token(authorization)
    settings = get_settings()
    if request.dry_run:
        return await dry_run_snapshot(request)
    if not settings.forebet_draw_booking_enabled:
        raise HTTPException(status_code=409, detail="Forebet draw booking is disabled")
    return await execute_snapshot(request)


class ForebetMatchesRequest(ForebetAnalyzeRequest):
    sportybet_events: list[SportyBetEvent]


@router.post("/analyze", response_model=ForebetAnalyzeResponse)
async def analyze_forebet(request: ForebetAnalyzeRequest) -> ForebetAnalyzeResponse:
    settings = get_settings()
    parsed = urlparse(request.url)
    base = urlparse(settings.forebet_base_url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname != base.hostname:
        raise HTTPException(status_code=422, detail="URL must belong to the configured Forebet domain")
    try:
        matches = parse_forebet_html(await fetch_forebet_page(request.url), request.url)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Unable to fetch Forebet page: {exc}") from exc
    draws = get_draw_matches(matches)
    return ForebetAnalyzeResponse(source_url=request.url, total_matches=len(matches), draw_count=len(draws), draw_matches=draws, matches=matches)


@router.post("/matches", response_model=list[FixtureMatchDateGroup])
async def match_forebet_draws(request: ForebetMatchesRequest) -> list[FixtureMatchDateGroup]:
    """Parse Forebet and match its explicit DRAW fixtures against supplied SportyBet events.

    SportyBet event retrieval is intentionally not duplicated here: the current
    SportyBet service exposes booking/share retrieval, not an upcoming-event
    catalogue endpoint. This route accepts normalized provider events so the
    pure matcher can be exercised safely until that provider capability exists.
    """
    try:
        html = await fetch_forebet_page(request.url)
        matches = parse_forebet_html(html, request.url)
        results = match_forebet_fixtures(matches, request.sportybet_events)
        grouped: dict[date, list] = {}
        for result in results:
            kickoff = result.forebet_match.kickoff
            if kickoff is None:
                continue
            match_date = kickoff.date() if isinstance(kickoff, datetime) else kickoff
            grouped.setdefault(match_date, []).append(result)
        return [FixtureMatchDateGroup(date=match_date, results=items) for match_date, items in sorted(grouped.items())]
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Unable to analyze Forebet fixtures: {exc}") from exc


@router.post("/book-draws", response_model=DrawBookingResponse)
async def book_draws(request: DrawBookingRequest) -> DrawBookingResponse:
    return await create_draw_booking(request.fixtures)


@router.get("/draw-window", response_model=DrawWindowResponse)
async def get_draw_window() -> DrawWindowResponse:
    response = forebet_draw_engine.get_active_window()
    response.prebooking_days = forebet_draw_engine.store.list_prebooking()
    response.compilation = forebet_draw_engine.store.get_compilation()
    return response


@router.get("/draw-window/diagnostics")
async def get_draw_window_diagnostics() -> dict:
    """Run the provider/matching pipeline without booking or state mutation."""
    return await run_forebet_draw_diagnostics()


@router.post("/draw-window/refresh", response_model=DrawWindowResponse)
async def refresh_draw_window(request: DrawWindowRefreshRequest) -> DrawWindowResponse:
    return await forebet_draw_engine.refresh_window(request.source_urls, request.start_datetime, request.end_datetime)


@router.post("/draw-window/{prediction_date}/refresh", response_model=DrawWindowResponse)
async def refresh_draw_window_day(prediction_date: date, request: DrawWindowRefreshRequest) -> DrawWindowResponse:
    return await forebet_draw_engine.refresh_day(prediction_date, request.source_urls, request.start_datetime, request.end_datetime)
