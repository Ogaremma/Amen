from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.prediction import EvidenceQuality, PredictionExplanation
from app.schemas.sportybet_markets import SportyBetSelectionIdentity


class PredictionBookingPool(str, Enum):
    qualifying = 'qualifying'
    predictions = 'predictions'


class PredictionBookingDay(str, Enum):
    today = 'today'
    tomorrow = 'tomorrow'


class PredictionBookingBatchStatus(str, Enum):
    created = 'created'
    reused = 'reused'
    empty = 'empty'
    failed = 'failed'


class PredictionBookingDayStatus(str, Enum):
    complete = 'complete'
    partial = 'partial'
    empty = 'empty'
    rejected = 'rejected'
    failed = 'failed'
    unavailable = 'unavailable'


class PredictionBookingResultStatus(str, Enum):
    complete = 'complete'
    partial = 'partial'
    unavailable = 'unavailable'


class PredictionBookingSelection(BaseModel):
    identity: SportyBetSelectionIdentity
    home_team: str
    away_team: str
    competition: str | None = None
    kickoff_at: datetime
    odds: float
    provider_probability: float | None = None
    match_status: str | None = None
    baseline_score: float | None = None
    evidence_score: float | None = None
    evidence_quality: EvidenceQuality | None = None
    explanation: PredictionExplanation | None = None


class PredictionBookingRejection(BaseModel):
    pool: PredictionBookingPool
    day: PredictionBookingDay | None = None
    identity: SportyBetSelectionIdentity | None = None
    event_id: str
    reason: str
    predicted_odds: float | None = None
    current_odds: float | None = None


class PredictionBookingBatch(BaseModel):
    pool: PredictionBookingPool
    day: PredictionBookingDay
    batch_index: int = Field(ge=1)
    selection_count: int = Field(ge=1)
    selections: list[PredictionBookingSelection]
    booking_code: str | None = None
    batch_identity: str
    status: PredictionBookingBatchStatus
    error: str | None = None


class PredictionBookingGroup(BaseModel):
    group_index: int = Field(ge=1)
    label: str
    selection_count: int = Field(ge=0)
    selections: list[PredictionBookingSelection] = Field(default_factory=list)
    batches: list[PredictionBookingBatch] = Field(default_factory=list)
    booking_codes: list[str] = Field(default_factory=list)
    status: PredictionBookingDayStatus


class PredictionBookingDayResult(BaseModel):
    pool: PredictionBookingPool
    day: PredictionBookingDay
    selection_count: int = Field(ge=0)
    selections: list[PredictionBookingSelection] = Field(default_factory=list)
    batches: list[PredictionBookingBatch] = Field(default_factory=list)
    booking_codes: list[str] = Field(default_factory=list)
    rejected: list[PredictionBookingRejection] = Field(default_factory=list)
    groups: list[PredictionBookingGroup] = Field(default_factory=list)
    status: PredictionBookingDayStatus


class PredictionBookingPoolResult(BaseModel):
    pool: PredictionBookingPool
    today: PredictionBookingDayResult
    tomorrow: PredictionBookingDayResult


class PredictionBookingCatalogueDiagnostics(BaseModel):
    expected_total: int
    retrieved_total: int
    parsed_fixtures: int
    pages_fetched: int
    pagination_complete: bool
    retrieved_at: datetime | None
    fresh: bool
    authoritative: bool
    error: str | None = None


class PredictionBookingResult(BaseModel):
    generated_at: datetime
    window_start: datetime
    window_end_exclusive: datetime
    catalogue: PredictionBookingCatalogueDiagnostics
    qualifying: PredictionBookingPoolResult
    predictions: PredictionBookingPoolResult
    status: PredictionBookingResultStatus
