from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException

from app.config.settings import get_settings
from app.schemas.forebet import ForebetAnalyzeRequest, ForebetAnalyzeResponse
from app.services.forebet import fetch_forebet_page, get_draw_matches, parse_forebet_html

router = APIRouter(prefix="/api/v1/forebet", tags=["forebet"])


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
