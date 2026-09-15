from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


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
