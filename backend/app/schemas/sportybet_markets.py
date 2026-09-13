from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


class SportyBetSelectionIdentity(BaseModel):
    event_id: str
    market_id: str
    outcome_id: str
    product_id: int
    sport_id: str
    specifier: str


class SportyBetOutcome(BaseModel):
    outcome_id: str
    description: str | None = None
    odds: float | None = None
    probability: float | None = None
    is_active: bool | None = None


class SportyBetMarket(BaseModel):
    market_id: str
    specifier: str | None = None
    product_id: int | None = None
    description: str | None = None
    name: str | None = None
    status: int | None = None
    is_banned: bool | None = None
    source_type: str | None = None
    last_odds_change_time: datetime | None = None
    outcomes: list[SportyBetOutcome] = Field(default_factory=list)


class SportyBetFixtureWithMarkets(BaseModel):
    event_id: str
    game_id: str | None = None
    sport_id: str | None = None
    sport_name: str | None = None
    category_id: str | None = None
    category_name: str | None = None
    tournament_id: str | None = None
    tournament_name: str | None = None
    competition: str | None = None
    home_team_id: str | None = None
    home_team_name: str
    away_team_id: str | None = None
    away_team_name: str
    kickoff: datetime
    status: int | None = None
    match_status: str | None = None
    is_banned: bool | None = None
    markets: list[SportyBetMarket] = Field(default_factory=list)


class SportyBetMarketCatalogPage(BaseModel):
    total_num: int
    fixtures: list[SportyBetFixtureWithMarkets] = Field(default_factory=list)
    pages_fetched: int = 1
    retrieved_at: datetime | None = None
    complete: bool = True
    retrieved_num: int | None = None
    more_events: bool | None = None

    def is_fresh(self, ttl_seconds: float, *, now: datetime | None = None) -> bool:
        if self.retrieved_at is None:
            return False
        current = now or datetime.now(timezone.utc)
        return (current - self.retrieved_at).total_seconds() <= ttl_seconds

    def diagnostics(self, ttl_seconds: float, *, now: datetime | None = None) -> dict:
        retrieved_total = self.retrieved_num if self.retrieved_num is not None else len(self.fixtures)
        fresh = self.is_fresh(ttl_seconds, now=now)
        return {
            "expected_total": self.total_num,
            "retrieved_total": retrieved_total,
            "parsed_fixtures": len(self.fixtures),
            "pages_fetched": self.pages_fetched,
            "pagination_complete": self.complete,
            "retrieved_at": self.retrieved_at.isoformat() if self.retrieved_at else None,
            "fresh": fresh,
            "stale": not fresh,
            "authoritative": self.complete and fresh,
        }


class OverOneHalfCandidate(BaseModel):
    identity: SportyBetSelectionIdentity
    fixture: SportyBetFixtureWithMarkets
    market: SportyBetMarket
    outcome: SportyBetOutcome
