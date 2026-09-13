from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from app.schemas.sportybet_markets import (
    OverOneHalfCandidate,
    SportyBetFixtureWithMarkets,
    SportyBetMarket,
    SportyBetMarketCatalogPage,
    SportyBetOutcome,
    SportyBetSelectionIdentity,
)

_BIZ_CODE_OK = 10000
OVER_ONE_HALF_MARKET_ID = "18"
OVER_ONE_HALF_SPECIFIER = "total=1.5"
OVER_ONE_HALF_OUTCOME_ID = "12"
OVER_ONE_HALF_OUTCOME_LABEL = "Over 1.5"
OVER_ONE_HALF_PRODUCT_ID = 3
OVER_ONE_HALF_MIN_ODDS = 1.40
OVER_ONE_HALF_MAX_ODDS_EXCLUSIVE = 1.50


def _to_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _to_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes"}:
            return True
        if normalized in {"0", "false", "no"}:
            return False
    return None


def _to_utc_datetime(value: Any) -> datetime | None:
    milliseconds = _to_int(value)
    if milliseconds is None:
        return None
    try:
        return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _parse_outcome(raw: Any) -> SportyBetOutcome | None:
    if not isinstance(raw, dict) or raw.get("id") is None:
        return None
    return SportyBetOutcome(
        outcome_id=str(raw["id"]),
        description=raw.get("desc") or raw.get("description"),
        odds=_to_float(raw.get("odds")),
        probability=_to_float(raw.get("probability")),
        is_active=_to_bool(raw.get("isActive")),
    )


def _parse_market(raw: Any) -> SportyBetMarket | None:
    if not isinstance(raw, dict) or raw.get("id") is None:
        return None
    outcomes = [
        parsed
        for outcome in raw.get("outcomes", []) or []
        if (parsed := _parse_outcome(outcome)) is not None
    ]
    return SportyBetMarket(
        market_id=str(raw["id"]),
        specifier=raw.get("specifier") or None,
        product_id=_to_int(raw.get("product")),
        description=raw.get("desc") or raw.get("description"),
        name=raw.get("name"),
        status=_to_int(raw.get("status")),
        is_banned=_to_bool(raw.get("banned")),
        source_type=raw.get("sourceType"),
        last_odds_change_time=_to_utc_datetime(raw.get("lastOddsChangeTime")),
        outcomes=outcomes,
    )


def _parse_fixture(raw: Any) -> SportyBetFixtureWithMarkets | None:
    if not isinstance(raw, dict):
        return None
    event_id = raw.get("eventId")
    home = raw.get("homeTeamName")
    away = raw.get("awayTeamName")
    start_ms = raw.get("estimateStartTime")
    if not event_id or not home or not away or start_ms is None:
        return None

    kickoff = _to_utc_datetime(start_ms)
    if kickoff is None:
        return None

    sport = raw.get("sport") if isinstance(raw.get("sport"), dict) else {}
    category = sport.get("category") if isinstance(sport.get("category"), dict) else {}
    tournament = (
        category.get("tournament")
        if isinstance(category.get("tournament"), dict)
        else {}
    )
    markets = [
        parsed
        for market in raw.get("markets", []) or []
        if (parsed := _parse_market(market)) is not None
    ]
    tournament_name = tournament.get("name")

    return SportyBetFixtureWithMarkets(
        event_id=str(event_id),
        game_id=str(raw["gameId"]) if raw.get("gameId") is not None else None,
        sport_id=sport.get("id"),
        sport_name=sport.get("name"),
        category_id=category.get("id"),
        category_name=category.get("name"),
        tournament_id=tournament.get("id"),
        tournament_name=tournament_name,
        competition=tournament_name,
        home_team_id=raw.get("homeTeamId"),
        home_team_name=str(home),
        away_team_id=raw.get("awayTeamId"),
        away_team_name=str(away),
        kickoff=kickoff,
        status=_to_int(raw.get("status")),
        match_status=raw.get("matchStatus"),
        is_banned=_to_bool(raw.get("banned")),
        markets=markets,
    )


def parse_upcoming_events_markets(payload: Any) -> SportyBetMarketCatalogPage:
    if not isinstance(payload, dict) or payload.get("bizCode") != _BIZ_CODE_OK:
        raise HTTPException(
            status_code=502, detail="Invalid SportyBet upcoming-events response"
        )
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("tournaments", []), list):
        raise HTTPException(
            status_code=502, detail="Invalid SportyBet upcoming-events response"
        )
    total_num = _to_int(data.get("totalNum"))
    more_events = data.get("moreEvents")
    if total_num is None and not isinstance(more_events, bool):
        raise HTTPException(
            status_code=502, detail="Invalid SportyBet upcoming-events response"
        )

    fixtures: list[SportyBetFixtureWithMarkets] = []
    for tournament in data.get("tournaments", []):
        if not isinstance(tournament, dict):
            continue
        for raw in tournament.get("events", []) or []:
            parsed = _parse_fixture(raw)
            if parsed is not None:
                fixtures.append(parsed)

    return SportyBetMarketCatalogPage(
        total_num=total_num or 0,
        fixtures=fixtures,
        retrieved_num=len(fixtures),
        complete=not (more_events is True),
        more_events=more_events if isinstance(more_events, bool) else None,
    )


def extract_over_one_half_candidates(
    fixtures: Iterable[SportyBetFixtureWithMarkets],
    *,
    min_odds: float = OVER_ONE_HALF_MIN_ODDS,
    max_odds_exclusive: float = OVER_ONE_HALF_MAX_ODDS_EXCLUSIVE,
) -> list[OverOneHalfCandidate]:
    if not math.isfinite(min_odds) or min_odds <= 0:
        raise ValueError("min_odds must be finite and positive")
    if not math.isfinite(max_odds_exclusive) or max_odds_exclusive <= min_odds:
        raise ValueError("max_odds_exclusive must be finite and greater than min_odds")

    candidates: list[OverOneHalfCandidate] = []
    seen_identities: set[tuple[str, str, str, int, str, str]] = set()
    for fixture in fixtures:
        if fixture.sport_id is None or fixture.is_banned is True:
            continue
        for market in fixture.markets:
            if (
                market.market_id != OVER_ONE_HALF_MARKET_ID
                or market.specifier != OVER_ONE_HALF_SPECIFIER
                or market.product_id != OVER_ONE_HALF_PRODUCT_ID
                or market.is_banned is True
            ):
                continue
            for outcome in market.outcomes:
                if (
                    outcome.outcome_id != OVER_ONE_HALF_OUTCOME_ID
                    or outcome.description is None
                    or outcome.description.strip() != OVER_ONE_HALF_OUTCOME_LABEL
                    or outcome.is_active is not True
                    or outcome.odds is None
                    or outcome.odds <= 0
                    or outcome.odds < min_odds
                    or outcome.odds >= max_odds_exclusive
                ):
                    continue

                identity = SportyBetSelectionIdentity(
                    event_id=fixture.event_id,
                    market_id=market.market_id,
                    outcome_id=outcome.outcome_id,
                    product_id=market.product_id,
                    sport_id=fixture.sport_id,
                    specifier=market.specifier or "",
                )
                identity_key = (
                    identity.event_id,
                    identity.market_id,
                    identity.outcome_id,
                    identity.product_id,
                    identity.sport_id,
                    identity.specifier,
                )
                if identity_key in seen_identities:
                    continue
                seen_identities.add(identity_key)
                candidates.append(
                    OverOneHalfCandidate(
                        identity=identity,
                        fixture=fixture,
                        market=market,
                        outcome=outcome,
                    )
                )
    return candidates
