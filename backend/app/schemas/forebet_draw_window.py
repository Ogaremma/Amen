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


class DrawBookingBatch(BaseModel):
    batch_index: int
    booking_code: str | None = None
    identity: str
    status: str
    matches: list[DrawWindowMatch]
    created_at: datetime | None = None
    updated_at: datetime | None = None


class DrawRebookEvent(BaseModel):
    scope: str
    batch_index: int | None = None
    removed: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    old_code: str | None = None
    new_code: str | None = None
    timestamp: datetime


class DrawWindowDay(BaseModel):
    prediction_date: date
    booking_code: str | None = None
    selection_count: int
    status: Literal["active", "unavailable", "complete", "error"]
    matches: list[DrawWindowMatch]
    source_urls: list[str] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)
    diagnostic_code: str | None = None
    diagnostic_message: str | None = None
    identity: str | None = None
    created_at: datetime
    last_updated: datetime
    acquisition: dict = Field(default_factory=dict)
    batches: list[DrawBookingBatch] = Field(default_factory=list)
    monitoring: dict = Field(default_factory=dict)
    rebook_events: list[DrawRebookEvent] = Field(default_factory=list)

class DrawCompilation(BaseModel):
    compilation_id: str
    booking_code: str | None = None
    selection_count: int
    prediction_dates: list[date]
    matches: list[DrawWindowMatch]
    status: Literal["active", "unavailable", "overflow", "error"]
    identity: str
    diagnostics: list[str] = Field(default_factory=list)
    diagnostic_code: str | None = None
    diagnostic_message: str | None = None
    created_at: datetime
    updated_at: datetime
    batches: list[DrawBookingBatch] = Field(default_factory=list)
    monitoring: dict = Field(default_factory=dict)
    rebook_events: list[DrawRebookEvent] = Field(default_factory=list)


class DrawWindowRefreshRequest(BaseModel):
    source_urls: list[str] = Field(min_length=1)
    start_datetime: datetime | None = None
    end_datetime: datetime | None = None


class DrawWindowResponse(BaseModel):
    days: list[DrawWindowDay]
    active_count: int
    prebooking_days: list[dict] = Field(default_factory=list)
    compilation: DrawCompilation | None = None
    target_dates: list[date] = Field(default_factory=list)
    acquisition: dict = Field(default_factory=dict)
