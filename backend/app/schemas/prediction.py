from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.sportybet_markets import SportyBetSelectionIdentity
from app.schemas.prediction_evidence import FootballFixtureEvidence


class PredictionStatus(str, Enum):
    pending = 'pending'
    live = 'live'
    settled_win = 'settled_win'
    settled_miss = 'settled_miss'
    postponed = 'postponed'
    cancelled = 'cancelled'
    abandoned = 'abandoned'
    unresolved = 'unresolved'
    won = 'won'
    lost = 'lost'
    void = 'void'


class EvidenceQuality(str, Enum):
    complete = 'complete'
    partial = 'partial'
    insufficient = 'insufficient'


class PredictionMarketFeatures(BaseModel):
    market_id: str
    specifier: str | None = None
    outcome_id: str
    product_id: int | None = None
    label: str | None = None
    market_description: str | None = None
    market_name: str | None = None
    market_status: int | None = None
    market_is_banned: bool | None = None
    odds: float | None = None
    provider_probability_source: float | None = None
    provider_probability: float | None = None
    active: bool | None = None
    last_odds_change_time: datetime | None = None
    source_type: str | None = None


class PredictionFixtureFeatures(BaseModel):
    event_id: str
    game_id: str | None = None
    sport_id: str | None = None
    sport_name: str | None = None
    category_id: str | None = None
    home_team: str
    home_team_id: str | None = None
    away_team: str
    away_team_id: str | None = None
    competition: str | None = None
    kickoff_at: datetime
    category_name: str | None = None
    tournament_id: str | None = None
    tournament_name: str | None = None
    status: int | None = None
    match_status: str | None = None
    is_banned: bool | None = None


class PredictionDataQuality(BaseModel):
    market_identity_verified: bool
    required_fields_present: bool
    active_outcome: bool
    valid_odds: bool
    target_odds_range: bool
    provider_probability_available: bool
    provider_probability_valid: bool
    fixture_complete: bool
    data_quality_complete: bool


class PredictionFeatureSnapshot(BaseModel):
    model_version: str
    identity: SportyBetSelectionIdentity
    market: PredictionMarketFeatures
    fixture: PredictionFixtureFeatures
    data_quality: PredictionDataQuality
    football_evidence: FootballFixtureEvidence | None = None


class PredictionScoreBreakdown(BaseModel):
    data_quality: float
    market_identity: float
    active_market: float
    target_odds_range: float
    odds_proximity: float
    provider_probability: float
    total: float


class EvidenceScoreBreakdown(BaseModel):
    market_quality: float
    provider_probability: float
    historical_over_1_5: float
    goal_production: float
    venue_context: float
    evidence_quality_multiplier: float
    total: float


class PredictionExplanation(BaseModel):
    evidence_quality: EvidenceQuality
    positive_signals: list[str] = Field(default_factory=list)
    negative_signals: list[str] = Field(default_factory=list)
    missing_signals: list[str] = Field(default_factory=list)
    final_score: float
    selection_decision: str


class PredictionWindow(BaseModel):
    start: datetime
    end_exclusive: datetime


class PredictionEvaluation(BaseModel):
    identity: SportyBetSelectionIdentity
    home_team: str
    away_team: str
    competition: str | None = None
    kickoff_at: datetime
    prediction_market: str
    odds: float
    baseline_score: float | None = None
    evidence_score: float | None = None
    evidence_quality: EvidenceQuality | None = None
    model_score: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    confidence_label: str
    confidence_basis: Literal['normalized_model_score', 'evidence_ranking_score']
    selected: bool
    reasons: list[str] = Field(default_factory=list)
    selection_reason: str
    score_breakdown: PredictionScoreBreakdown
    evidence_score_breakdown: EvidenceScoreBreakdown | None = None
    explanation: PredictionExplanation | None = None
    feature_snapshot: PredictionFeatureSnapshot
    model_version: str
    provider_probability: float | None = None
    provider_probability_source: float | None = None


class PredictionPoolResult(BaseModel):
    window: PredictionWindow
    qualifying: list[PredictionEvaluation]
    predictions: list[PredictionEvaluation]


class PredictionRecord(BaseModel):
    id: int
    event_id: str
    sport_id: str
    market_id: str
    outcome_id: str
    product_id: int
    specifier: str
    home_team: str
    away_team: str
    competition: str | None = None
    kickoff_at: datetime
    prediction_market: str
    odds_at_prediction: float
    baseline_score: float | None = None
    evidence_score: float | None = None
    evidence_quality: EvidenceQuality | None = None
    model_score: float
    confidence: float
    selection_reason: str
    provider_probability: float | None = None
    provider_probability_source: float | None = None
    explanation: PredictionExplanation | None = None
    booking_pool: str | None = None
    booking_code: str | None = None
    booking_batch_identity: str | None = None
    booking_created_at: datetime | None = None
    feature_snapshot: PredictionFeatureSnapshot
    prediction_status: PredictionStatus
    live_status: str | None = None
    current_home_goals: int | None = None
    current_away_goals: int | None = None
    last_score_update_at: datetime | None = None
    actual_goals: int | None = None
    actual_result: str | None = None
    settled_at: datetime | None = None
    model_version: str
    created_at: datetime
    updated_at: datetime


class PredictionSaveResult(BaseModel):
    inserted: int
    skipped_existing: int
    ignored_not_selected: int
