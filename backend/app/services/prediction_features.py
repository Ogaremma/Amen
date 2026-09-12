from __future__ import annotations

import math
from datetime import datetime

from app.schemas.prediction import (
    PredictionDataQuality,
    PredictionFeatureSnapshot,
    PredictionFixtureFeatures,
    PredictionMarketFeatures,
)
from app.schemas.prediction_evidence import FootballFixtureEvidence
from app.schemas.sportybet_markets import OverOneHalfCandidate
from app.services.sportybet_markets import (
    OVER_ONE_HALF_MARKET_ID,
    OVER_ONE_HALF_MAX_ODDS_EXCLUSIVE,
    OVER_ONE_HALF_MIN_ODDS,
    OVER_ONE_HALF_OUTCOME_ID,
    OVER_ONE_HALF_OUTCOME_LABEL,
    OVER_ONE_HALF_PRODUCT_ID,
    OVER_ONE_HALF_SPECIFIER,
)

PREDICTION_MODEL_VERSION = 'baseline-v1'
PREDICTION_MARKET = 'over_1_5'


def _present(value: object | None) -> bool:
    return value is not None and str(value).strip() != ''


def _valid_probability(value: float | None) -> bool:
    return value is not None and math.isfinite(value) and 0 <= value <= 1


def extract_prediction_features(
    candidate: OverOneHalfCandidate,
    football_evidence: FootballFixtureEvidence | None = None,
    model_version: str = PREDICTION_MODEL_VERSION,
) -> PredictionFeatureSnapshot:
    fixture = candidate.fixture
    market = candidate.market
    outcome = candidate.outcome
    identity = candidate.identity

    market_identity_verified = (
        identity.event_id == fixture.event_id
        and identity.market_id == market.market_id
        and identity.outcome_id == outcome.outcome_id
        and identity.product_id == market.product_id
        and identity.sport_id == fixture.sport_id
        and identity.specifier == (market.specifier or '')
        and market.market_id == OVER_ONE_HALF_MARKET_ID
        and market.specifier == OVER_ONE_HALF_SPECIFIER
        and market.product_id == OVER_ONE_HALF_PRODUCT_ID
        and outcome.outcome_id == OVER_ONE_HALF_OUTCOME_ID
        and (outcome.description or '').strip() == OVER_ONE_HALF_OUTCOME_LABEL
        and market.is_banned is not True
    )

    required_fields_present = all(
        _present(value)
        for value in (
            identity.event_id,
            identity.market_id,
            identity.outcome_id,
            identity.product_id,
            identity.sport_id,
            identity.specifier,
            fixture.event_id,
            fixture.sport_id,
            fixture.home_team_name,
            fixture.away_team_name,
            fixture.kickoff,
            market.market_id,
            market.specifier,
            market.product_id,
            outcome.outcome_id,
            outcome.description,
        )
    )

    valid_odds = outcome.odds is not None and math.isfinite(outcome.odds) and outcome.odds > 0
    target_odds_range = (
        valid_odds
        and outcome.odds is not None
        and OVER_ONE_HALF_MIN_ODDS <= outcome.odds < OVER_ONE_HALF_MAX_ODDS_EXCLUSIVE
    )
    provider_probability_available = outcome.probability is not None
    provider_probability_valid = _valid_probability(outcome.probability)
    fixture_complete = (
        _present(fixture.event_id)
        and _present(fixture.sport_id)
        and _present(fixture.home_team_name)
        and _present(fixture.away_team_name)
        and fixture.kickoff is not None
        and fixture.is_banned is not True
    )

    data_quality = PredictionDataQuality(
        market_identity_verified=market_identity_verified,
        required_fields_present=required_fields_present,
        active_outcome=outcome.is_active is True,
        valid_odds=valid_odds,
        target_odds_range=target_odds_range,
        provider_probability_available=provider_probability_available,
        provider_probability_valid=provider_probability_valid,
        fixture_complete=fixture_complete,
        data_quality_complete=(
            market_identity_verified
            and required_fields_present
            and outcome.is_active is True
            and valid_odds
            and target_odds_range
            and fixture_complete
        ),
    )

    market_features = PredictionMarketFeatures(
        market_id=market.market_id,
        specifier=market.specifier,
        outcome_id=outcome.outcome_id,
        product_id=market.product_id,
        label=(outcome.description or '').strip() or None,
        market_description=market.description,
        market_name=market.name,
        market_status=market.status,
        market_is_banned=market.is_banned,
        odds=outcome.odds,
        provider_probability_source=outcome.probability,
        provider_probability=outcome.probability if provider_probability_valid else None,
        active=outcome.is_active is True,
        last_odds_change_time=market.last_odds_change_time,
        source_type=market.source_type,
    )

    fixture_features = PredictionFixtureFeatures(
        event_id=fixture.event_id,
        game_id=fixture.game_id,
        sport_id=fixture.sport_id,
        sport_name=fixture.sport_name,
        category_id=fixture.category_id,
        category_name=fixture.category_name,
        tournament_id=fixture.tournament_id,
        tournament_name=fixture.tournament_name,
        home_team=fixture.home_team_name,
        home_team_id=fixture.home_team_id,
        away_team=fixture.away_team_name,
        away_team_id=fixture.away_team_id,
        competition=fixture.competition,
        kickoff_at=fixture.kickoff,
        status=fixture.status,
        match_status=fixture.match_status,
        is_banned=fixture.is_banned,
    )

    return PredictionFeatureSnapshot(
        model_version=model_version,
        identity=identity,
        market=market_features,
        fixture=fixture_features,
        data_quality=data_quality,
        football_evidence=football_evidence,
    )
