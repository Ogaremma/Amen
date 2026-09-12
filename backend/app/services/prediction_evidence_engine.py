from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from app.schemas.prediction import (
    EvidenceQuality,
    EvidenceScoreBreakdown,
    PredictionEvaluation,
    PredictionExplanation,
)
from app.schemas.prediction_evidence import FootballFixtureEvidence, FootballTeamForm
from app.schemas.sportybet_markets import OverOneHalfCandidate
from app.services.prediction_engine import score_baseline_features
from app.services.prediction_features import (
    PREDICTION_MARKET,
    extract_prediction_features,
)
from app.services.prediction_evidence import is_evidence_fresh
from app.services.sportybet_markets import (
    OVER_ONE_HALF_MAX_ODDS_EXCLUSIVE,
    OVER_ONE_HALF_MIN_ODDS,
)

EVIDENCE_MODEL_VERSION = 'evidence-v1'
EVIDENCE_MIN_SCORE = 0.70
EVIDENCE_MIN_QUALITY = EvidenceQuality.partial
EVIDENCE_MIN_RECENT_SAMPLE = 3
EVIDENCE_COMPLETE_RECENT_SAMPLE = 5

EVIDENCE_WEIGHT_MARKET_QUALITY = 0.35
EVIDENCE_WEIGHT_PROVIDER_PROBABILITY = 0.15
EVIDENCE_WEIGHT_HISTORICAL_OVER = 0.25
EVIDENCE_WEIGHT_GOAL_PRODUCTION = 0.15
EVIDENCE_WEIGHT_VENUE_CONTEXT = 0.10

EVIDENCE_QUALITY_MULTIPLIER = {
    EvidenceQuality.complete: 1.0,
    EvidenceQuality.partial: 0.75,
    EvidenceQuality.insufficient: 0.25,
}


@dataclass(frozen=True)
class _RateSignal:
    value: float | None
    sample_size: int
    valid: bool


@dataclass(frozen=True)
class _GoalSignal:
    value: float | None
    sample_size: int
    expected_goals: float | None
    valid: bool


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _confidence_label(score: float, quality: EvidenceQuality) -> str:
    if quality == EvidenceQuality.insufficient:
        return 'Insufficient evidence'
    if score >= 0.80:
        return 'High-ranked candidate'
    if score >= EVIDENCE_MIN_SCORE:
        return 'Evidence-supported candidate'
    if score >= 0.55:
        return 'Partial evidence'
    return 'Insufficient evidence'


def _market_valid(feature_snapshot) -> bool:
    quality = feature_snapshot.data_quality
    return (
        quality.market_identity_verified
        and quality.required_fields_present
        and quality.active_outcome
        and quality.valid_odds
        and quality.target_odds_range
        and quality.fixture_complete
        and feature_snapshot.market.market_is_banned is not True
        and feature_snapshot.fixture.is_banned is not True
    )


def _market_quality(feature_snapshot) -> float:
    if not _market_valid(feature_snapshot):
        return 0.0
    odds = feature_snapshot.market.odds
    if odds is None:
        return 0.0
    proximity = 1 - (
        (odds - OVER_ONE_HALF_MIN_ODDS)
        / (OVER_ONE_HALF_MAX_ODDS_EXCLUSIVE - OVER_ONE_HALF_MIN_ODDS)
    )
    return _clamp01(0.70 + 0.30 * proximity)


def _rate_signal(form: FootballTeamForm | None) -> _RateSignal:
    if form is None:
        return _RateSignal(value=None, sample_size=0, valid=False)
    last_10 = form.over_1_5_rate_last_10
    last_5 = form.over_1_5_rate_last_5
    if last_10.valid and last_10.sample_size >= EVIDENCE_COMPLETE_RECENT_SAMPLE:
        reliability = min(last_10.sample_size, 10) / 10
        return _RateSignal(
            value=last_10.value * reliability if last_10.value is not None else None,
            sample_size=last_10.sample_size,
            valid=True,
        )
    if last_5.valid:
        reliability = min(last_5.sample_size, 5) / 5
        return _RateSignal(
            value=last_5.value * reliability if last_5.value is not None else None,
            sample_size=last_5.sample_size,
            valid=True,
        )
    return _RateSignal(value=None, sample_size=0, valid=False)


def _goal_signal(form: FootballTeamForm | None) -> _GoalSignal:
    if form is None:
        return _GoalSignal(value=None, sample_size=0, expected_goals=None, valid=False)

    recent_features = []
    if form.goals_scored_average_last_10.valid and form.goals_scored_average_last_10.sample_size >= EVIDENCE_COMPLETE_RECENT_SAMPLE:
        recent_features = [
            form.goals_scored_average_last_10,
            form.goals_conceded_average_last_10,
        ]
    elif form.goals_scored_average_last_5.valid:
        recent_features = [
            form.goals_scored_average_last_5,
            form.goals_conceded_average_last_5,
        ]

    if recent_features:
        scored = recent_features[0].value
        conceded = recent_features[1].value
        sample_size = recent_features[0].sample_size
        if scored is not None and conceded is not None:
            expected_goals = scored + conceded
            reliability = min(sample_size, 10) / 10
            return _GoalSignal(
                value=_clamp01(expected_goals / 2.5) * reliability,
                sample_size=sample_size,
                expected_goals=expected_goals,
                valid=True,
            )

    season_stats = form.season_stats
    if season_stats is not None:
        scored = season_stats.goals_scored_average.value
        conceded = season_stats.goals_conceded_average.value
        sample_size = min(
            season_stats.goals_scored_average.sample_size,
            season_stats.goals_conceded_average.sample_size,
        )
        if scored is not None and conceded is not None:
            expected_goals = scored + conceded
            reliability = min(sample_size, 10) / 10
            return _GoalSignal(
                value=_clamp01(expected_goals / 2.5) * reliability,
                sample_size=sample_size,
                expected_goals=expected_goals,
                valid=True,
            )

    return _GoalSignal(value=None, sample_size=0, expected_goals=None, valid=False)


def _venue_signal(evidence: FootballFixtureEvidence) -> _RateSignal:
    home_venue = evidence.home_form.home if evidence.home_form is not None else None
    away_venue = evidence.away_form.away if evidence.away_form is not None else None
    if home_venue is None or away_venue is None:
        return _RateSignal(value=None, sample_size=0, valid=False)
    if not home_venue.over_1_5_rate.valid or not away_venue.over_1_5_rate.valid:
        return _RateSignal(value=None, sample_size=0, valid=False)

    home_reliability = min(home_venue.over_1_5_rate.sample_size, 5) / 5
    away_reliability = min(away_venue.over_1_5_rate.sample_size, 5) / 5
    if home_reliability + away_reliability <= 0:
        return _RateSignal(value=None, sample_size=0, valid=False)
    home_value = home_venue.over_1_5_rate.value or 0.0
    away_value = away_venue.over_1_5_rate.value or 0.0
    value = (
        home_value * home_reliability + away_value * away_reliability
    ) / (home_reliability + away_reliability)
    sample_size = min(
        home_venue.over_1_5_rate.sample_size,
        away_venue.over_1_5_rate.sample_size,
    )
    return _RateSignal(value=value, sample_size=sample_size, valid=True)


def _contains_future_result(feature_snapshot) -> bool:
    fixture_kickoff = feature_snapshot.fixture.kickoff_at
    forms = []
    evidence = feature_snapshot.football_evidence
    if evidence is not None:
        forms = [form for form in (evidence.home_form, evidence.away_form) if form is not None]
    return any(
        match.kickoff_at >= fixture_kickoff
        for form in forms
        for match in form.last_10
    )


def _evidence_quality(
    feature_snapshot,
    *,
    evidence_is_fresh: bool,
) -> EvidenceQuality:
    evidence = feature_snapshot.football_evidence
    if evidence is None or not evidence_is_fresh or _contains_future_result(feature_snapshot):
        return EvidenceQuality.insufficient

    home_signal = _rate_signal(evidence.home_form)
    away_signal = _rate_signal(evidence.away_form)
    if (
        not home_signal.valid
        or not away_signal.valid
        or home_signal.sample_size < EVIDENCE_MIN_RECENT_SAMPLE
        or away_signal.sample_size < EVIDENCE_MIN_RECENT_SAMPLE
    ):
        return EvidenceQuality.insufficient

    complete = (
        home_signal.sample_size >= EVIDENCE_COMPLETE_RECENT_SAMPLE
        and away_signal.sample_size >= EVIDENCE_COMPLETE_RECENT_SAMPLE
        and evidence.home_form is not None
        and evidence.away_form is not None
        and evidence.home_form.home is not None
        and evidence.away_form.away is not None
        and _venue_signal(evidence).valid
        and evidence.data_quality.season_statistics_available
        and evidence.data_quality.provider_probability_available
        and evidence.data_quality.competition_available
        and evidence.data_quality.complete
    )
    return EvidenceQuality.complete if complete else EvidenceQuality.partial


def score_evidence_features(
    feature_snapshot,
    *,
    evidence_is_fresh: bool = True,
) -> EvidenceScoreBreakdown:
    evidence = feature_snapshot.football_evidence
    quality = _evidence_quality(
        feature_snapshot,
        evidence_is_fresh=evidence_is_fresh,
    )
    usable_evidence = evidence_is_fresh and quality != EvidenceQuality.insufficient
    quality_multiplier = (
        EVIDENCE_QUALITY_MULTIPLIER[quality] if usable_evidence else 0.0
    )

    market_quality = EVIDENCE_WEIGHT_MARKET_QUALITY * _market_quality(feature_snapshot)
    provider_probability = 0.0
    if feature_snapshot.data_quality.provider_probability_valid:
        provider_value = feature_snapshot.market.provider_probability
        if provider_value is not None:
            provider_probability = EVIDENCE_WEIGHT_PROVIDER_PROBABILITY * provider_value

    historical_over = 0.0
    goal_production = 0.0
    venue_context = 0.0
    if evidence is not None and usable_evidence:
        home_over = _rate_signal(evidence.home_form)
        away_over = _rate_signal(evidence.away_form)
        historical_value = ((home_over.value or 0.0) + (away_over.value or 0.0)) / 2
        historical_over = (
            EVIDENCE_WEIGHT_HISTORICAL_OVER * historical_value * quality_multiplier
        )

        home_goals = _goal_signal(evidence.home_form)
        away_goals = _goal_signal(evidence.away_form)
        goal_value = ((home_goals.value or 0.0) + (away_goals.value or 0.0)) / 2
        goal_production = (
            EVIDENCE_WEIGHT_GOAL_PRODUCTION * goal_value * quality_multiplier
        )

        venue = _venue_signal(evidence)
        venue_context = (
            EVIDENCE_WEIGHT_VENUE_CONTEXT * (venue.value or 0.0) * quality_multiplier
        )

    total = _clamp01(
        market_quality
        + provider_probability
        + historical_over
        + goal_production
        + venue_context
    )
    return EvidenceScoreBreakdown(
        market_quality=round(market_quality, 4),
        provider_probability=round(provider_probability, 4),
        historical_over_1_5=round(historical_over, 4),
        goal_production=round(goal_production, 4),
        venue_context=round(venue_context, 4),
        evidence_quality_multiplier=round(quality_multiplier, 4),
        total=round(total, 4),
    )


def _selection_decision(
    *,
    market_valid: bool,
    quality: EvidenceQuality,
    score: float,
    threshold: float,
    minimum_quality: EvidenceQuality,
) -> str:
    if not market_valid:
        return 'excluded'
    allowed_quality = {EvidenceQuality.partial, EvidenceQuality.complete}
    if minimum_quality == EvidenceQuality.complete:
        allowed_quality = {EvidenceQuality.complete}
    if quality not in allowed_quality:
        return 'insufficient evidence quality'
    if score < threshold:
        return 'below evidence-v1 threshold'
    return 'selected'


def _explanation(
    feature_snapshot,
    breakdown: EvidenceScoreBreakdown,
    *,
    quality: EvidenceQuality,
    evidence_is_fresh: bool,
    selected: bool,
    threshold: float,
) -> PredictionExplanation:
    evidence = feature_snapshot.football_evidence
    market_valid = _market_valid(feature_snapshot)
    positive: list[str] = []
    negative: list[str] = []
    missing: list[str] = []

    odds = feature_snapshot.market.odds
    if market_valid and odds is not None:
        positive.append(
            f'The active Over 1.5 market at {odds:.2f} odds is inside the 1.40 to 1.49 target range.'
        )
    elif feature_snapshot.data_quality.active_outcome is False:
        negative.append('The Over 1.5 outcome is inactive.')
    else:
        negative.append('The market is missing required validity, identity, or target-range evidence.')

    provider_source = feature_snapshot.market.provider_probability_source
    if feature_snapshot.data_quality.provider_probability_valid:
        provider_value = feature_snapshot.market.provider_probability
        positive.append(
            'SportyBet supplied a provider probability of '
            f'{provider_value:.4f}; it is used only as a bookmaker signal, not as an Amen-calibrated probability.'
        )
    elif provider_source is not None:
        negative.append(
            f'The supplied SportyBet probability {provider_source:.4f} is invalid and is not used.'
        )
    else:
        missing.append('SportyBet provider probability is unavailable.')

    if evidence is None:
        missing.append('Football evidence is unavailable.')
    else:
        if not evidence_is_fresh:
            negative.append('Football evidence is stale and is not used.')
        if _contains_future_result(feature_snapshot):
            negative.append('Football evidence contains a result at or after prediction kickoff and is not used.')

        home_over = _rate_signal(evidence.home_form)
        away_over = _rate_signal(evidence.away_form)
        if evidence.home_form is None:
            missing.append('Home-team recent form is unavailable.')
        if evidence.away_form is None:
            missing.append('Away-team recent form is unavailable.')

        if home_over.valid and evidence.home_form is not None:
            rate = evidence.home_form.over_1_5_rate_last_5.value
            if rate is not None and rate >= 0.5:
                positive.append(
                    f'Home recent Over 1.5 rate is {rate:.0%} over {home_over.sample_size} matches.'
                )
            elif rate is not None:
                negative.append(
                    f'Home recent Over 1.5 rate is weak at {rate:.0%} over {home_over.sample_size} matches.'
                )
        if away_over.valid and evidence.away_form is not None:
            rate = evidence.away_form.over_1_5_rate_last_5.value
            if rate is not None and rate >= 0.5:
                positive.append(
                    f'Away recent Over 1.5 rate is {rate:.0%} over {away_over.sample_size} matches.'
                )
            elif rate is not None:
                negative.append(
                    f'Away recent Over 1.5 rate is weak at {rate:.0%} over {away_over.sample_size} matches.'
                )

        if home_over.valid and away_over.valid:
            if home_over.sample_size < EVIDENCE_COMPLETE_RECENT_SAMPLE:
                negative.append(f'Home historical sample is small ({home_over.sample_size} matches).')
            if away_over.sample_size < EVIDENCE_COMPLETE_RECENT_SAMPLE:
                negative.append(f'Away historical sample is small ({away_over.sample_size} matches).')
            if (
                evidence.home_form is not None
                and evidence.away_form is not None
                and evidence.home_form.over_1_5_rate_last_5.value is not None
                and evidence.away_form.over_1_5_rate_last_5.value is not None
                and abs(
                    evidence.home_form.over_1_5_rate_last_5.value
                    - evidence.away_form.over_1_5_rate_last_5.value
                )
                >= 0.25
            ):
                negative.append('Home and away recent Over 1.5 evidence conflicts.')

        home_goals = _goal_signal(evidence.home_form)
        away_goals = _goal_signal(evidence.away_form)
        if home_goals.valid and home_goals.expected_goals is not None and home_goals.expected_goals >= 2.0:
            positive.append(
                f'Home matches average {home_goals.expected_goals:.2f} total goals over {home_goals.sample_size} matches.'
            )
        elif home_goals.valid and home_goals.expected_goals is not None:
            negative.append(
                f'Home matches average only {home_goals.expected_goals:.2f} total goals over {home_goals.sample_size} matches.'
            )
        else:
            missing.append('Home goal history is unavailable.')
        if away_goals.valid and away_goals.expected_goals is not None and away_goals.expected_goals >= 2.0:
            positive.append(
                f'Away matches average {away_goals.expected_goals:.2f} total goals over {away_goals.sample_size} matches.'
            )
        elif away_goals.valid and away_goals.expected_goals is not None:
            negative.append(
                f'Away matches average only {away_goals.expected_goals:.2f} total goals over {away_goals.sample_size} matches.'
            )
        else:
            missing.append('Away goal history is unavailable.')

        venue = _venue_signal(evidence)
        if venue.valid:
            positive.append('Home and away venue-specific Over 1.5 splits are available.')
        else:
            missing.append('Venue-specific home/away splits are unavailable.')
        if evidence.home_form is not None and evidence.home_form.season_stats is None:
            missing.append('Home season statistics are unavailable.')
        if evidence.away_form is not None and evidence.away_form.season_stats is None:
            missing.append('Away season statistics are unavailable.')

    if quality == EvidenceQuality.insufficient:
        negative.append('Evidence quality is insufficient for evidence-v1 selection.')
    elif quality == EvidenceQuality.partial:
        negative.append('Evidence quality is partial; football evidence is down-weighted.')
    else:
        positive.append('Evidence quality is complete.')

    decision = 'selected' if selected else 'not selected'
    if not market_valid:
        decision = 'excluded'
    positive.append(
        f'evidence-v1 ranking score is {breakdown.total:.4f}; this is not a calibrated probability.'
    )
    negative.append(
        f'Final decision: {decision} against the {threshold:.2f} selection threshold.'
    )

    return PredictionExplanation(
        evidence_quality=quality,
        positive_signals=positive,
        negative_signals=negative,
        missing_signals=missing,
        final_score=breakdown.total,
        selection_decision=decision,
    )


def evaluate_evidence_prediction(
    candidate: OverOneHalfCandidate,
    *,
    football_evidence: FootballFixtureEvidence | None = None,
    min_score: float = EVIDENCE_MIN_SCORE,
    min_quality: EvidenceQuality = EVIDENCE_MIN_QUALITY,
    now: datetime | None = None,
) -> PredictionEvaluation:
    evidence_is_fresh = (
        football_evidence is None or is_evidence_fresh(football_evidence, now=now)
    )
    feature_snapshot = extract_prediction_features(
        candidate,
        football_evidence,
        model_version=EVIDENCE_MODEL_VERSION,
    )
    baseline_breakdown = score_baseline_features(feature_snapshot)
    evidence_breakdown = score_evidence_features(
        feature_snapshot,
        evidence_is_fresh=evidence_is_fresh,
    )
    quality = _evidence_quality(
        feature_snapshot,
        evidence_is_fresh=evidence_is_fresh,
    )
    market_valid = _market_valid(feature_snapshot)
    allowed_quality = {EvidenceQuality.partial, EvidenceQuality.complete}
    if min_quality == EvidenceQuality.complete:
        allowed_quality = {EvidenceQuality.complete}
    selected = (
        market_valid
        and quality in allowed_quality
        and evidence_breakdown.total >= min_score
    )
    explanation = _explanation(
        feature_snapshot,
        evidence_breakdown,
        quality=quality,
        evidence_is_fresh=evidence_is_fresh,
        selected=selected,
        threshold=min_score,
    )
    reasons = (
        explanation.positive_signals
        + explanation.negative_signals
        + explanation.missing_signals
    )

    return PredictionEvaluation(
        identity=feature_snapshot.identity,
        home_team=feature_snapshot.fixture.home_team,
        away_team=feature_snapshot.fixture.away_team,
        competition=feature_snapshot.fixture.competition,
        kickoff_at=feature_snapshot.fixture.kickoff_at,
        prediction_market=PREDICTION_MARKET,
        odds=feature_snapshot.market.odds or 0.0,
        baseline_score=baseline_breakdown.total,
        evidence_score=evidence_breakdown.total,
        evidence_quality=quality,
        model_score=evidence_breakdown.total,
        confidence=evidence_breakdown.total,
        confidence_label=_confidence_label(evidence_breakdown.total, quality),
        confidence_basis='evidence_ranking_score',
        selected=selected,
        reasons=reasons,
        selection_reason=' '.join(reasons),
        score_breakdown=baseline_breakdown,
        evidence_score_breakdown=evidence_breakdown,
        explanation=explanation,
        feature_snapshot=feature_snapshot,
        model_version=EVIDENCE_MODEL_VERSION,
        provider_probability=feature_snapshot.market.provider_probability,
        provider_probability_source=feature_snapshot.market.provider_probability_source,
    )


def evaluate_evidence_predictions(
    candidates: Iterable[OverOneHalfCandidate],
    *,
    football_evidence_by_event_id: dict[str, FootballFixtureEvidence] | None = None,
    min_score: float = EVIDENCE_MIN_SCORE,
    min_quality: EvidenceQuality = EVIDENCE_MIN_QUALITY,
    now: datetime | None = None,
) -> list[PredictionEvaluation]:
    return [
        evaluate_evidence_prediction(
            candidate,
            football_evidence=(
                football_evidence_by_event_id.get(candidate.identity.event_id)
                if football_evidence_by_event_id is not None
                else None
            ),
            min_score=min_score,
            min_quality=min_quality,
            now=now,
        )
        for candidate in candidates
    ]
