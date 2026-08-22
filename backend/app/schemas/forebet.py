from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, Field


class ForebetPredictionResult(str, Enum):
    HOME = "HOME"
    DRAW = "DRAW"
    AWAY = "AWAY"
    UNKNOWN = "UNKNOWN"


class ForebetProbability(BaseModel):
    home: float | None = None
    draw: float | None = None
    away: float | None = None


class ForebetMatch(BaseModel):
    match_id: str | None = None
    home_team: str
    away_team: str
    competition: str | None = None
    country: str | None = None
    competition_code: str | None = None
    kickoff: datetime | date | None = None
    kickoff_display: str | None = None
    match_url: str | None = None
    predicted_result: ForebetPredictionResult = ForebetPredictionResult.UNKNOWN
    predicted_score_home: int | None = None
    predicted_score_away: int | None = None
    probabilities: ForebetProbability | None = None
    average_goals: float | None = None
    primary_coefficient: float | None = None
    odds_home: float | None = None
    odds_draw: float | None = None
    odds_away: float | None = None
    narrative: str | None = None
    source: str = "forebet"
    source_url: str | None = None


class ForebetAnalyzeRequest(BaseModel):
    url: str = Field(min_length=1)


class ForebetAnalyzeResponse(BaseModel):
    source_url: str
    total_matches: int
    draw_count: int
    draw_matches: list[ForebetMatch]
    matches: list[ForebetMatch]


class SportyBetEvent(BaseModel):
    event_id: str
    home_team: str
    away_team: str
    home_team_name: str | None = None
    away_team_name: str | None = None
    kickoff: datetime
    competition: str | None = None
    game_id: str | None = None
    home_team_id: str | None = None
    away_team_id: str | None = None
    sport_id: str | None = None
    sport_name: str | None = None
    category_id: str | None = None
    category_name: str | None = None
    tournament_id: str | None = None
    tournament_name: str | None = None
    status: int | None = None
    match_status: str | None = None
    market_id: str | None = None
    product_id: int | None = None
    specifier: str | None = None
    outcome_home_id: str | None = None
    outcome_draw_id: str | None = None
    outcome_away_id: str | None = None
    odds_home: float | None = None
    odds_draw: float | None = None
    odds_away: float | None = None
    probability_home: float | None = None
    probability_draw: float | None = None
    probability_away: float | None = None
    source: str = "sportybet"


class FixtureMatchStatus(str, Enum):
    MATCHED_EXACT = "MATCHED_EXACT"
    MATCHED_NORMALIZED = "MATCHED_NORMALIZED"
    MATCHED_FUZZY = "MATCHED_FUZZY"
    UNMATCHED = "UNMATCHED"
    AMBIGUOUS = "AMBIGUOUS"


class FixtureMatchResult(BaseModel):
    forebet_match: ForebetMatch
    status: FixtureMatchStatus
    matching_method: str | None = None
    matching_confidence: float | None = None
    sportybet_event: SportyBetEvent | None = None
    candidates: list[SportyBetEvent] = Field(default_factory=list)
    reason: str | None = None
    home_similarity: float | None = None
    away_similarity: float | None = None
    minimum_team_similarity: float | None = None
    average_team_similarity: float | None = None
    competition_similarity: float | None = None
    kickoff_delta_hours: float | None = None
    same_lagos_date: bool | None = None
    same_direction: bool | None = None
    candidate_margin: float | None = None

class FixtureMatchDateGroup(BaseModel):
    date: date
    results: list[FixtureMatchResult]


class DrawBookingRequest(BaseModel):
    fixtures: list[FixtureMatchResult] = Field(min_length=1)


class DrawBookingResponse(BaseModel):
    booking_code: str
    selection_count: int
    event_ids: list[str]
    teams: list[str]
    source_dates: list[date]
    total_odds: float | None = None
