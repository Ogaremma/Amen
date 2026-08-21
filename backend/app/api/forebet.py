from __future__ import annotations

from datetime import date, datetime
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException

from app.config.settings import get_settings
from app.schemas.forebet import ForebetAnalyzeRequest, ForebetAnalyzeResponse, FixtureMatchDateGroup, SportyBetEvent
from app.services.fixture_matching import match_forebet_fixtures
from app.services.forebet import fetch_forebet_page, get_draw_matches, parse_forebet_html

router = APIRouter(prefix="/api/v1/forebet", tags=["forebet"])


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
