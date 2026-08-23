from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class DrawWindowMatch(BaseModel):
    event_id: str
    home_team: str
    away_team: str
    kickoff: datetime
    match_status: str | None = None
    market_id: str
    outcome_id: str
    product_id: int
    sport_id: str
    specifier: str | None = None


class DrawWindowDay(BaseModel):
    prediction_date: date
    booking_code: str | None = None
    selection_count: int
    status: Literal["active", "unavailable", "complete", "error"]
    matches: list[DrawWindowMatch]
    source_urls: list[str] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)
    created_at: datetime
    last_updated: datetime

class DrawCompilation(BaseModel):
    compilation_id: str
    booking_code: str | None = None
    selection_count: int
    prediction_dates: list[date]
    matches: list[DrawWindowMatch]
    status: Literal["active", "unavailable", "overflow", "error"]
    identity: str
    diagnostics: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class DrawWindowRefreshRequest(BaseModel):
    source_urls: list[str] = Field(min_length=1)
    start_datetime: datetime | None = None
    end_datetime: datetime | None = None


class DrawWindowResponse(BaseModel):
    days: list[DrawWindowDay]
    active_count: int
    prebooking_days: list[dict] = Field(default_factory=list)
    compilation: DrawCompilation | None = None
