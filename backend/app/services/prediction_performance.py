from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timezone
from statistics import fmean, median
from typing import Iterable, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.schemas.prediction import EvidenceQuality, PredictionRecord, PredictionStatus
from app.schemas.prediction_performance import (
    PredictionLearningAssessment,
    PredictionLearningStatus,
    PredictionPerformanceFilters,
    PredictionPerformanceGroup,
    PredictionPerformanceMetrics,
    PredictionPerformanceReport,
    PredictionPerformanceSampleStatus,
    PredictionPoolComparison,
    PredictionProviderCalibrationBin,
    PredictionProviderProbabilityAnalysis,
    PredictionTemporalSplit,
    WilsonConfidenceInterval,
)
from app.services.prediction_store import PredictionStore, prediction_store


_WILSON_Z = 1.959963984540054
_PERFORMANCE_DATE_TIMEZONE_NAME = 'Africa/Lagos'
try:
    _PERFORMANCE_DATE_TIMEZONE = ZoneInfo(_PERFORMANCE_DATE_TIMEZONE_NAME)
except ZoneInfoNotFoundError:
    from datetime import timedelta

    _PERFORMANCE_DATE_TIMEZONE = timezone(timedelta(hours=1), name='Africa/Lagos')

DEFAULT_MIN_GROUP_SAMPLE = 5
DEFAULT_MIN_ANALYSIS_SAMPLE = 30
DEFAULT_MIN_CANDIDATE_SAMPLE = 100

_SETTLED_STATUSES = {
    PredictionStatus.settled_win,
    PredictionStatus.settled_miss,
}


def _utc_now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _utc_kickoff(record: PredictionRecord) -> datetime:
    kickoff = record.kickoff_at
    if kickoff.tzinfo is None:
        return kickoff.replace(tzinfo=timezone.utc)
    return kickoff.astimezone(timezone.utc)


def _is_settled(record: PredictionRecord) -> bool:
    return record.prediction_status in _SETTLED_STATUSES


def _is_win(record: PredictionRecord) -> bool:
    return record.prediction_status == PredictionStatus.settled_win


def _valid_evidence_score(record: PredictionRecord) -> bool:
    return (
        record.evidence_score is not None
        and math.isfinite(record.evidence_score)
        and 0.0 <= record.evidence_score <= 1.0
    )


def _valid_odds(record: PredictionRecord) -> bool:
    return (
        record.odds_at_prediction is not None
        and math.isfinite(record.odds_at_prediction)
        and record.odds_at_prediction > 0.0
    )


def _valid_provider_probability(record: PredictionRecord) -> bool:
    return (
        record.provider_probability is not None
        and math.isfinite(record.provider_probability)
        and 0.0 <= record.provider_probability <= 1.0
    )


def wilson_confidence_interval(
    wins: int,
    settled: int,
    *,
    confidence_level: float = 0.95,
) -> WilsonConfidenceInterval | None:
    if settled <= 0 or wins < 0 or wins > settled:
        return None
    if not 0.0 < confidence_level < 1.0:
        raise ValueError('confidence_level must be between 0 and 1')

    z_value = {
        0.90: 1.6448536269514722,
        0.95: _WILSON_Z,
        0.99: 2.5758293035489004,
    }.get(confidence_level)
    if z_value is None:
        raise ValueError('Unsupported Wilson confidence level')

    observed = wins / settled
    denominator = 1.0 + z_value * z_value / settled
    center = (observed + z_value * z_value / (2.0 * settled)) / denominator
    margin = (
        z_value
        * math.sqrt(
            observed * (1.0 - observed) / settled
            + z_value * z_value / (4.0 * settled * settled)
        )
        / denominator
    )
    return WilsonConfidenceInterval(
        confidence_level=confidence_level,
        lower=max(0.0, center - margin),
        upper=min(1.0, center + margin),
    )


def _sample_status(sample_size: int, minimum: int) -> PredictionPerformanceSampleStatus:
    return (
        PredictionPerformanceSampleStatus.sufficient_sample
        if sample_size >= minimum
        else PredictionPerformanceSampleStatus.insufficient_sample
    )


def _metrics(records: Sequence[PredictionRecord]) -> PredictionPerformanceMetrics:
    settled = [record for record in records if _is_settled(record)]
    wins = sum(1 for record in settled if _is_win(record))
    misses = len(settled) - wins
    evidence_scores = [
        record.evidence_score
        for record in settled
        if _valid_evidence_score(record)
    ]
    odds = [
        record.odds_at_prediction
        for record in settled
        if _valid_odds(record)
    ]
    non_settled: dict[str, int] = defaultdict(int)
    for record in records:
        if not _is_settled(record):
            non_settled[record.prediction_status.value] += 1

    return PredictionPerformanceMetrics(
        total_records=len(records),
        total_settled=len(settled),
        wins=wins,
        misses=misses,
        win_rate=(wins / len(settled) if settled else None),
        confidence_interval=wilson_confidence_interval(wins, len(settled)),
        non_settled_by_status=dict(non_settled),
        average_evidence_score=(fmean(evidence_scores) if evidence_scores else None),
        median_evidence_score=(median(evidence_scores) if evidence_scores else None),
        evidence_score_sample_size=len(evidence_scores),
        average_odds=(fmean(odds) if odds else None),
        odds_sample_size=len(odds),
    )


def _group(
    records: Sequence[PredictionRecord],
    *,
    key: str,
    label: str,
    minimum_sample: int,
) -> PredictionPerformanceGroup:
    settled = [record for record in records if _is_settled(record)]
    metrics = _metrics(settled)
    return PredictionPerformanceGroup(
        key=key,
        label=label,
        total_settled=metrics.total_settled,
        wins=metrics.wins,
        misses=metrics.misses,
        win_rate=metrics.win_rate,
        confidence_interval=metrics.confidence_interval,
        average_evidence_score=metrics.average_evidence_score,
        median_evidence_score=metrics.median_evidence_score,
        evidence_score_sample_size=metrics.evidence_score_sample_size,
        average_odds=metrics.average_odds,
        odds_sample_size=metrics.odds_sample_size,
        sample_status=_sample_status(metrics.total_settled, minimum_sample),
    )


def _grouped(
    records: Sequence[PredictionRecord],
    key_function,
    *,
    minimum_sample: int,
) -> list[PredictionPerformanceGroup]:
    grouped_records: dict[str, list[PredictionRecord]] = defaultdict(list)
    labels: dict[str, str] = {}
    for record in records:
        if not _is_settled(record):
            continue
        key, label = key_function(record)
        grouped_records[key].append(record)
        labels[key] = label
    return [
        _group(grouped_records[key], key=key, label=labels[key], minimum_sample=minimum_sample)
        for key in sorted(grouped_records)
    ]


def _evidence_score_band(record: PredictionRecord) -> tuple[str, str]:
    if not _valid_evidence_score(record):
        return 'unavailable', 'Evidence score unavailable'
    score = record.evidence_score
    if score < 0.70:
        return 'below_0_70', 'Below 0.70'
    if score < 0.75:
        return '0_70_0_74', '0.70–0.74'
    if score < 0.80:
        return '0_75_0_79', '0.75–0.79'
    if score < 0.85:
        return '0_80_0_84', '0.80–0.84'
    if score < 0.90:
        return '0_85_0_89', '0.85–0.89'
    return '0_90_1_00', '0.90–1.00'


def _odds_band(record: PredictionRecord) -> tuple[str, str]:
    if not _valid_odds(record):
        return 'unavailable', 'Odds unavailable'
    odds = record.odds_at_prediction
    if 1.40 <= odds < 1.45:
        return '1_40_1_44', '1.40–1.44'
    if 1.45 <= odds < 1.50:
        return '1_45_1_49', '1.45–1.49'
    return 'outside_target_range', 'Outside target range'


def _date_group(record: PredictionRecord) -> tuple[str, str]:
    local_date = _utc_kickoff(record).astimezone(_PERFORMANCE_DATE_TIMEZONE).date()
    return local_date.isoformat(), local_date.isoformat()


def _competition_group(record: PredictionRecord) -> tuple[str, str]:
    competition = (record.competition or 'Unknown competition').strip()
    return competition.casefold(), competition


def _model_version_group(record: PredictionRecord) -> tuple[str, str]:
    return record.model_version, record.model_version


def _evidence_quality_group(record: PredictionRecord) -> tuple[str, str]:
    quality = record.evidence_quality or EvidenceQuality.insufficient
    return quality.value, quality.value.replace('_', ' ').title()


def _provider_probability_analysis(
    records: Sequence[PredictionRecord],
    *,
    minimum_sample: int,
) -> PredictionProviderProbabilityAnalysis:
    settled = [record for record in records if _is_settled(record)]
    valid = [record for record in settled if _valid_provider_probability(record)]
    wins = sum(1 for record in valid if _is_win(record))
    probabilities = [record.provider_probability for record in valid]
    brier_score = None
    if valid:
        brier_score = fmean(
            (
                (record.provider_probability - (1.0 if _is_win(record) else 0.0)) ** 2
                for record in valid
            )
        )

    bins: dict[int, list[PredictionRecord]] = defaultdict(list)
    for record in valid:
        index = min(9, int(record.provider_probability * 10))
        bins[index].append(record)

    reliability_bins = []
    for index in sorted(bins):
        bin_records = bins[index]
        bin_wins = sum(1 for record in bin_records if _is_win(record))
        reliability_bins.append(
            PredictionProviderCalibrationBin(
                key=f'{index / 10:.1f}-{(index + 1) / 10:.1f}',
                label=f'{index / 10:.2f}–{(index + 1) / 10 - 0.01:.2f}',
                lower_bound=index / 10,
                upper_bound_exclusive=(index + 1) / 10,
                total_settled=len(bin_records),
                wins=bin_wins,
                misses=len(bin_records) - bin_wins,
                observed_win_rate=(bin_wins / len(bin_records) if bin_records else None),
                average_provider_probability=fmean(
                    record.provider_probability for record in bin_records
                ),
                sample_status=_sample_status(len(bin_records), minimum_sample),
            )
        )

    return PredictionProviderProbabilityAnalysis(
        valid_sample_size=len(valid),
        missing_sample_size=len(settled) - len(valid),
        wins=wins,
        misses=len(valid) - wins,
        observed_win_rate=(wins / len(valid) if valid else None),
        average_provider_probability=(fmean(probabilities) if probabilities else None),
        brier_score=brier_score,
        reliability_bins=reliability_bins,
        sample_status=_sample_status(len(valid), minimum_sample),
    )


def _pool_comparison(
    prediction_metrics: PredictionPerformanceMetrics,
    qualifying_metrics: PredictionPerformanceMetrics | None,
    *,
    minimum_sample: int,
) -> PredictionPoolComparison:
    if (
        qualifying_metrics is None
        or prediction_metrics.win_rate is None
        or qualifying_metrics.win_rate is None
    ):
        return PredictionPoolComparison(
            sample_status=PredictionPerformanceSampleStatus.insufficient_sample
        )
    sufficient = (
        prediction_metrics.total_settled >= minimum_sample
        and qualifying_metrics.total_settled >= minimum_sample
    )
    return PredictionPoolComparison(
        observed_win_rate_difference=(
            prediction_metrics.win_rate - qualifying_metrics.win_rate
        ),
        sample_status=(
            PredictionPerformanceSampleStatus.sufficient_sample
            if sufficient
            else PredictionPerformanceSampleStatus.insufficient_sample
        ),
    )


def _temporal_split(
    records: Sequence[PredictionRecord],
    *,
    minimum_candidate_sample: int,
) -> PredictionTemporalSplit | None:
    settled = sorted(
        (record for record in records if _is_settled(record)),
        key=lambda record: (_utc_kickoff(record), record.id),
    )
    if len(settled) < minimum_candidate_sample:
        return None
    training_count = int(len(settled) * 0.60)
    validation_count = int(len(settled) * 0.20)
    training = settled[:training_count]
    validation = settled[training_count:training_count + validation_count]
    holdout = settled[training_count + validation_count:]
    return PredictionTemporalSplit(
        training_settled=len(training),
        validation_settled=len(validation),
        holdout_settled=len(holdout),
        training_start=(_utc_kickoff(training[0]) if training else None),
        training_end=(_utc_kickoff(training[-1]) if training else None),
        validation_start=(_utc_kickoff(validation[0]) if validation else None),
        validation_end=(_utc_kickoff(validation[-1]) if validation else None),
        holdout_start=(_utc_kickoff(holdout[0]) if holdout else None),
        holdout_end=(_utc_kickoff(holdout[-1]) if holdout else None),
    )


def _learning_assessment(
    records: Sequence[PredictionRecord],
    *,
    minimum_analysis_sample: int,
    minimum_candidate_sample: int,
) -> PredictionLearningAssessment:
    settled = [record for record in records if _is_settled(record)]
    current_record = max(
        records,
        key=lambda record: (_utc_kickoff(record), record.id),
        default=None,
    )
    if len(settled) < minimum_analysis_sample:
        status = PredictionLearningStatus.insufficient_sample
        message = 'Not enough settled predictions for calibration analysis or model changes.'
    elif len(settled) < minimum_candidate_sample:
        status = PredictionLearningStatus.calibration_exploration
        message = 'Enough settled predictions for calibration exploration, but not candidate model evaluation.'
    else:
        status = PredictionLearningStatus.candidate_evaluation_ready
        message = 'Enough settled predictions for chronological candidate evaluation; no candidate is activated.'

    return PredictionLearningAssessment(
        status=status,
        current_model=(current_record.model_version if current_record else None),
        settled=len(settled),
        minimum_analysis_sample=minimum_analysis_sample,
        minimum_candidate_sample=minimum_candidate_sample,
        candidate_model=None,
        active_model_changed=False,
        message=message,
        temporal_split=(
            _temporal_split(
                records,
                minimum_candidate_sample=minimum_candidate_sample,
            )
            if len(settled) >= minimum_candidate_sample
            else None
        ),
    )


def _matches_filters(
    record: PredictionRecord,
    *,
    start: datetime | None,
    end: datetime | None,
    model_version: str | None,
    evidence_quality: EvidenceQuality | None,
    competition: str | None,
) -> bool:
    kickoff = _utc_kickoff(record)
    if start is not None and kickoff < _utc_now(start):
        return False
    if end is not None and kickoff >= _utc_now(end):
        return False
    if model_version is not None and record.model_version != model_version:
        return False
    if evidence_quality is not None and record.evidence_quality != evidence_quality:
        return False
    if competition is not None and (
        record.competition is None
        or record.competition.casefold() != competition.strip().casefold()
    ):
        return False
    return True


class PredictionPerformanceService:
    def __init__(
        self,
        store: PredictionStore | None = None,
        *,
        minimum_group_sample: int = DEFAULT_MIN_GROUP_SAMPLE,
        minimum_analysis_sample: int = DEFAULT_MIN_ANALYSIS_SAMPLE,
        minimum_candidate_sample: int = DEFAULT_MIN_CANDIDATE_SAMPLE,
    ) -> None:
        if minimum_group_sample < 1:
            raise ValueError('minimum_group_sample must be positive')
        if minimum_analysis_sample < 1:
            raise ValueError('minimum_analysis_sample must be positive')
        if minimum_candidate_sample < minimum_analysis_sample:
            raise ValueError('minimum_candidate_sample must be at least minimum_analysis_sample')
        self.store = store or prediction_store
        self.minimum_group_sample = minimum_group_sample
        self.minimum_analysis_sample = minimum_analysis_sample
        self.minimum_candidate_sample = minimum_candidate_sample

    def analyze_prediction_performance(
        self,
        *,
        records: Iterable[PredictionRecord] | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        model_version: str | None = None,
        evidence_quality: EvidenceQuality | None = None,
        competition: str | None = None,
        now: datetime | None = None,
    ) -> PredictionPerformanceReport:
        source_records = list(records) if records is not None else self.store.list_predictions()
        filtered = [
            record
            for record in source_records
            if _matches_filters(
                record,
                start=start,
                end=end,
                model_version=model_version,
                evidence_quality=evidence_quality,
                competition=competition,
            )
        ]
        qualifying_records = [
            record for record in filtered if record.booking_pool == 'qualifying'
        ]
        prediction_records = [
            record for record in filtered if record.booking_pool != 'qualifying'
        ]
        prediction_metrics = _metrics(prediction_records)
        qualifying_metrics = (
            _metrics(qualifying_records) if qualifying_records else None
        )

        return PredictionPerformanceReport(
            generated_at=_utc_now(now),
            date_timezone=_PERFORMANCE_DATE_TIMEZONE_NAME,
            filters=PredictionPerformanceFilters(
                start=(_utc_now(start) if start is not None else None),
                end=(_utc_now(end) if end is not None else None),
                model_version=model_version,
                evidence_quality=evidence_quality,
                competition=competition,
            ),
            prediction_pool=prediction_metrics,
            qualifying_pool=qualifying_metrics,
            pool_comparison=_pool_comparison(
                prediction_metrics,
                qualifying_metrics,
                minimum_sample=self.minimum_group_sample,
            ),
            model_versions=_grouped(
                prediction_records,
                _model_version_group,
                minimum_sample=self.minimum_group_sample,
            ),
            evidence_qualities=_grouped(
                prediction_records,
                _evidence_quality_group,
                minimum_sample=self.minimum_group_sample,
            ),
            evidence_score_bands=_grouped(
                prediction_records,
                _evidence_score_band,
                minimum_sample=self.minimum_group_sample,
            ),
            odds_bands=_grouped(
                prediction_records,
                _odds_band,
                minimum_sample=self.minimum_group_sample,
            ),
            dates=_grouped(
                prediction_records,
                _date_group,
                minimum_sample=self.minimum_group_sample,
            ),
            competitions=_grouped(
                prediction_records,
                _competition_group,
                minimum_sample=self.minimum_group_sample,
            ),
            provider_probability=_provider_probability_analysis(
                prediction_records,
                minimum_sample=self.minimum_group_sample,
            ),
            learning=_learning_assessment(
                prediction_records,
                minimum_analysis_sample=self.minimum_analysis_sample,
                minimum_candidate_sample=self.minimum_candidate_sample,
            ),
        )


prediction_performance_service = PredictionPerformanceService()
