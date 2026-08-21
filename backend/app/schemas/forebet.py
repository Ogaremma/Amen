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
