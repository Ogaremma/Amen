from __future__ import annotations

from collections.abc import Iterable

from app.schemas.prediction import (
    PredictionEvaluation,
    PredictionScoreBreakdown,
)
from app.schemas.sportybet_markets import OverOneHalfCandidate
from app.services.prediction_features import (
    PREDICTION_MARKET,
    PREDICTION_MODEL_VERSION,
    extract_prediction_features,
)
from app.services.sportybet_markets import (
    OVER_ONE_HALF_MAX_ODDS_EXCLUSIVE,
    OVER_ONE_HALF_MIN_ODDS,
)

BASELINE_MIN_SCORE = 0.80

SCORE_WEIGHT_DATA_QUALITY = 0.25
SCORE_WEIGHT_MARKET_IDENTITY = 0.20
SCORE_WEIGHT_ACTIVE_MARKET = 0.10
SCORE_WEIGHT_TARGET_ODDS_RANGE = 0.15
SCORE_WEIGHT_ODDS_PROXIMITY = 0.20
SCORE_WEIGHT_PROVIDER_PROBABILITY = 0.10


def _confidence_label(score: float) -> str:
    if score >= 0.85:
        return 'High confidence'
    if score >= 0.80:
        return 'Strong candidate'
    if score >= 0.65:
        return 'Moderate confidence'
    return 'Insufficient evidence'


def score_baseline_features(feature_snapshot) -> PredictionScoreBreakdown:
    quality = feature_snapshot.data_quality
    odds = feature_snapshot.market.odds

    data_quality_score = SCORE_WEIGHT_DATA_QUALITY if quality.data_quality_complete else 0.0
    market_identity_score = SCORE_WEIGHT_MARKET_IDENTITY if quality.market_identity_verified else 0.0
    active_market_score = SCORE_WEIGHT_ACTIVE_MARKET if quality.active_outcome else 0.0
    target_odds_score = SCORE_WEIGHT_TARGET_ODDS_RANGE if quality.target_odds_range else 0.0

    if quality.target_odds_range and odds is not None:
        normalized_distance = (odds - OVER_ONE_HALF_MIN_ODDS) / (
            OVER_ONE_HALF_MAX_ODDS_EXCLUSIVE - OVER_ONE_HALF_MIN_ODDS
        )
        odds_proximity_score = SCORE_WEIGHT_ODDS_PROXIMITY * (1 - normalized_distance)
    else:
        odds_proximity_score = 0.0

    provider_probability_score = (
        SCORE_WEIGHT_PROVIDER_PROBABILITY if quality.provider_probability_valid else 0.0
    )

    total = (
        data_quality_score
        + market_identity_score
        + active_market_score
        + target_odds_score
        + odds_proximity_score
        + provider_probability_score
    )

    return PredictionScoreBreakdown(
        data_quality=round(data_quality_score, 4),
        market_identity=round(market_identity_score, 4),
        active_market=round(active_market_score, 4),
        target_odds_range=round(target_odds_score, 4),
        odds_proximity=round(odds_proximity_score, 4),
        provider_probability=round(provider_probability_score, 4),
        total=round(total, 4),
    )


_score_features = score_baseline_features


def _reasons(feature_snapshot, score: float, selected: bool, threshold: float) -> list[str]:
    quality = feature_snapshot.data_quality
    odds = feature_snapshot.market.odds
    reasons: list[str] = []

    if not quality.data_quality_complete:
        reasons.append('The fixture is missing required verified market or identity data.')
    if not quality.market_identity_verified:
        reasons.append('The Over 1.5 market identity did not match the verified SportyBet contract.')
    if not quality.active_outcome:
        reasons.append('The Over 1.5 outcome is inactive.')
    if not quality.valid_odds:
        reasons.append('The Over 1.5 odds are missing or invalid.')
    elif not quality.target_odds_range:
        reasons.append('The Over 1.5 odds are outside the 1.40 to 1.49 target range.')

    if quality.provider_probability_valid:
        reasons.append('SportyBet supplied a valid Over 1.5 probability.')

    if selected:
        reasons.append(
            'Over 1.5 selected because the fixture has a valid active Over 1.5 market '
            f'at {odds:.2f} odds and complete market/fixture identity. '
            'The current baseline has sufficient verified market evidence.'
        )
    else:
        reasons.append(
            f'The baseline model score {score:.2f} is below the {threshold:.2f} selection threshold.'
        )

    return reasons


def evaluate_prediction(
    candidate: OverOneHalfCandidate,
    *,
    min_score: float = BASELINE_MIN_SCORE,
    football_evidence=None,
) -> PredictionEvaluation:
    feature_snapshot = extract_prediction_features(candidate, football_evidence)
    score_breakdown = score_baseline_features(feature_snapshot)
    score = score_breakdown.total
    selected = score >= min_score
    reasons = _reasons(feature_snapshot, score, selected, min_score)

    return PredictionEvaluation(
        identity=feature_snapshot.identity,
        home_team=feature_snapshot.fixture.home_team,
        away_team=feature_snapshot.fixture.away_team,
        competition=feature_snapshot.fixture.competition,
        kickoff_at=feature_snapshot.fixture.kickoff_at,
        prediction_market=PREDICTION_MARKET,
        odds=feature_snapshot.market.odds or 0.0,
        baseline_score=score,
        model_score=score,
        confidence=score,
        confidence_label=_confidence_label(score),
        confidence_basis='normalized_model_score',
        selected=selected,
        reasons=reasons,
        selection_reason=' '.join(reasons),
        score_breakdown=score_breakdown,
        feature_snapshot=feature_snapshot,
        model_version=PREDICTION_MODEL_VERSION,
        provider_probability=feature_snapshot.market.provider_probability,
        provider_probability_source=feature_snapshot.market.provider_probability_source,
    )


def evaluate_predictions(
    candidates: Iterable[OverOneHalfCandidate],
    *,
    min_score: float = BASELINE_MIN_SCORE,
    football_evidence_by_event_id=None,
) -> list[PredictionEvaluation]:
    return [
        evaluate_prediction(
            candidate,
            min_score=min_score,
            football_evidence=(
                football_evidence_by_event_id.get(candidate.identity.event_id)
                if football_evidence_by_event_id is not None
                else None
            ),
        )
        for candidate in candidates
    ]
