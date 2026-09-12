from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.sportybet_markets import SportyBetSelectionIdentity


class EvidenceSourceMetadata(BaseModel):
    source: str
    feed: str
    retrieved_at: datetime
    max_age_seconds: int | None = None
    source_identifier: str | None = None
    fixture_identifier: str | None = None
    team_id: str | None = None
    note: str | None = None


class FootballMatchResult(BaseModel):
    match_id: str
    kickoff_at: datetime
    team_id: str
    team_name: str
    opponent_id: str | None = None
    opponent_name: str
    is_home: bool
    goals_for: int
    goals_against: int
    total_goals: int
    over_1_5: bool
    competition: str | None = None
    tournament_id: str | None = None
    season_id: str | None = None
    source: EvidenceSourceMetadata


class FootballSeasonStats(BaseModel):
    team_id: str
    team_name: str
    season_id: str
    goals_scored_average: FootballAverageFeature
    goals_conceded_average: FootballAverageFeature
    source: EvidenceSourceMetadata


class FootballAverageFeature(BaseModel):
    value: float | None
    sample_size: int
    valid: bool
    source: str
    retrieved_at: datetime


class FootballRateFeature(BaseModel):
    value: float | None
    sample_size: int
    valid: bool
    source: str
    retrieved_at: datetime


class FootballTeamForm(BaseModel):
    team_id: str
    team_name: str
    last_5: list[FootballMatchResult] = Field(default_factory=list)
    last_10: list[FootballMatchResult] = Field(default_factory=list)
    goals_scored_average_last_5: FootballAverageFeature
    goals_scored_average_last_10: FootballAverageFeature
    goals_conceded_average_last_5: FootballAverageFeature
    goals_conceded_average_last_10: FootballAverageFeature
    over_1_5_rate_last_5: FootballRateFeature
    over_1_5_rate_last_10: FootballRateFeature
    home: FootballVenueForm | None = None
    away: FootballVenueForm | None = None
    season_stats: FootballSeasonStats | None = None
    source: EvidenceSourceMetadata


class FootballVenueForm(BaseModel):
    team_id: str
    team_name: str
    venue: Literal['home', 'away']
    matches: list[FootballMatchResult] = Field(default_factory=list)
    goals_scored_average: FootballAverageFeature
    goals_conceded_average: FootballAverageFeature
    over_1_5_rate: FootballRateFeature
    source: EvidenceSourceMetadata


class FootballMarketEvidence(BaseModel):
    identity: SportyBetSelectionIdentity
    odds: float | None
    provider_probability_source: float | None = None
    provider_probability: float | None
    provider_probability_basis: Literal['sportybet_decimal_probability']
    active: bool
    market_identity_verified: bool
    market_is_banned: bool
    fixture_is_banned: bool


class FootballFixtureContext(BaseModel):
    sportybet_event_id: str
    sportybet_source_id: str | None = None
    sportybet_home_team_id: str | None = None
    sportybet_away_team_id: str | None = None
    match_id: str
    home_team_id: str
    home_team_name: str
    away_team_id: str
    away_team_name: str
    competition: str | None = None
    tournament_id: str | None = None
    unique_tournament_id: str | None = None
    season_id: str | None = None
    source: EvidenceSourceMetadata


class FootballEvidenceDataQuality(BaseModel):
    market_available: bool
    fixture_matched: bool
    home_recent_form_available: bool
    away_recent_form_available: bool
    goal_history_available: bool
    season_statistics_available: bool
    home_away_split_available: bool
    competition_available: bool
    provider_probability_available: bool
    complete: bool
    missing: list[str] = Field(default_factory=list)
    available_feature_count: int
    total_feature_count: int


class FootballFixtureEvidence(BaseModel):
    context: FootballFixtureContext
    home_form: FootballTeamForm | None = None
    away_form: FootballTeamForm | None = None
    market: FootballMarketEvidence
    data_quality: FootballEvidenceDataQuality


class FootballEvidenceCollection(BaseModel):
    evidence_by_event_id: dict[str, FootballFixtureEvidence] = Field(default_factory=dict)
    unavailable_event_ids: list[str] = Field(default_factory=list)
    errors: dict[str, str] = Field(default_factory=dict)
