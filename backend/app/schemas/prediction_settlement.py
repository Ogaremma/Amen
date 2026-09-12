from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from app.schemas.prediction import PredictionStatus


class PredictionSettlementApplyOutcome(str, Enum):
    updated = 'updated'
    skipped_already_settled = 'skipped_already_settled'
    conflict = 'conflict'


class PredictionMatchStatus(str, Enum):
    final = 'final'
    live = 'live'
    upcoming = 'upcoming'
    postponed = 'postponed'
    cancelled = 'cancelled'
    abandoned = 'abandoned'
    unknown = 'unknown'


class PredictionSettlementOutcome(str, Enum):
    not_due = 'not_due'
    live = 'live'
    settled_win = 'settled_win'
    settled_miss = 'settled_miss'
    postponed = 'postponed'
    cancelled = 'cancelled'
    abandoned = 'abandoned'
    unresolved = 'unresolved'
    provider_failure = 'provider_failure'
    skipped_already_settled = 'skipped_already_settled'
    conflict = 'conflict'


class PredictionSettlementUpdate(BaseModel):
    prediction_id: int
    event_id: str
    prediction_status: PredictionStatus
    live_status: str | None = None
    current_home_goals: int | None = Field(default=None, ge=0)
    current_away_goals: int | None = Field(default=None, ge=0)
    actual_goals: int | None = Field(default=None, ge=0)
    actual_result: str | None = None
    settled_at: datetime | None = None
    reason: str | None = None


class PredictionSettlementDiagnostic(BaseModel):
    prediction_id: int
    event_id: str
    booking_code: str | None = None
    outcome: PredictionSettlementOutcome
    prediction_status: PredictionStatus
    home_score: int | None = Field(default=None, ge=0)
    away_score: int | None = Field(default=None, ge=0)
    actual_goals: int | None = Field(default=None, ge=0)
    reason: str | None = None


class PredictionSettlementSummary(BaseModel):
    checked: int = Field(default=0, ge=0)
    not_due: int = Field(default=0, ge=0)
    live: int = Field(default=0, ge=0)
    settled_win: int = Field(default=0, ge=0)
    settled_miss: int = Field(default=0, ge=0)
    postponed: int = Field(default=0, ge=0)
    cancelled: int = Field(default=0, ge=0)
    abandoned: int = Field(default=0, ge=0)
    unresolved: int = Field(default=0, ge=0)
    provider_failures: int = Field(default=0, ge=0)
    skipped_already_settled: int = Field(default=0, ge=0)
    provider_calls: int = Field(default=0, ge=0)
    generated_at: datetime
    diagnostics: list[PredictionSettlementDiagnostic] = Field(default_factory=list)
