from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from app.schemas.prediction import EvidenceQuality, PredictionStatus
from app.services.prediction_engine import evaluate_prediction
from app.services.prediction_performance import (
    PredictionPerformanceService,
    wilson_confidence_interval,
)
from app.schemas.prediction import PredictionRecord
from backend.tests.test_prediction_core_unittest import make_candidate


UTC = timezone.utc
NOW = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)


def performance_record(
    index: int,
    *,
    status: PredictionStatus = PredictionStatus.settled_win,
    odds: float = 1.43,
    evidence_score: float | None = 0.80,
    evidence_quality: EvidenceQuality | None = EvidenceQuality.partial,
    model_version: str = 'evidence-v1',
    provider_probability: float | None = 0.70,
    competition: str = 'League One',
    kickoff: datetime | None = None,
    booking_pool: str | None = 'predictions',
) -> PredictionRecord:
    kickoff = kickoff or NOW + timedelta(days=index)
    candidate = make_candidate(
        event_id=f'sr:match:performance-{index}',
        kickoff=kickoff,
        odds=odds,
        probability=provider_probability,
        competition=competition,
    )
    evaluation = evaluate_prediction(candidate)
    settled = status in {
        PredictionStatus.settled_win,
        PredictionStatus.settled_miss,
    }
    win = status == PredictionStatus.settled_win
    return PredictionRecord(
        id=index,
        event_id=candidate.identity.event_id,
        sport_id=candidate.identity.sport_id,
        market_id=candidate.identity.market_id,
        outcome_id=candidate.identity.outcome_id,
        product_id=candidate.identity.product_id,
        specifier=candidate.identity.specifier,
        home_team=evaluation.home_team,
        away_team=evaluation.away_team,
        competition=competition,
        kickoff_at=kickoff,
        prediction_market=evaluation.prediction_market,
        odds_at_prediction=odds,
        baseline_score=evaluation.baseline_score,
        evidence_score=evidence_score,
        evidence_quality=evidence_quality,
        model_score=evaluation.model_score,
        confidence=evaluation.confidence,
        selection_reason=evaluation.selection_reason,
        provider_probability=provider_probability,
        provider_probability_source=provider_probability,
        explanation=evaluation.explanation,
        booking_pool=booking_pool,
        booking_code=(f'CODE{index}' if booking_pool else None),
        booking_batch_identity=(f'BATCH{index}' if booking_pool else None),
        booking_created_at=(NOW if booking_pool else None),
        feature_snapshot=evaluation.feature_snapshot,
        prediction_status=status,
        actual_goals=(2 if win else 1) if settled else None,
        actual_result=('win' if win else 'miss') if settled else None,
        settled_at=(NOW if settled else None),
        model_version=model_version,
        created_at=NOW,
        updated_at=NOW,
    )


def service(**kwargs) -> PredictionPerformanceService:
    defaults = {
        'store': mock.Mock(),
        'minimum_group_sample': 1,
        'minimum_analysis_sample': 3,
        'minimum_candidate_sample': 5,
    }
    defaults.update(kwargs)
    return PredictionPerformanceService(**defaults)


class WilsonIntervalTests(unittest.TestCase):
    def test_zero_sample_has_no_interval(self):
        self.assertIsNone(wilson_confidence_interval(0, 0))

    def test_tiny_sample_interval_is_conservative(self):
        interval = wilson_confidence_interval(1, 1)
        self.assertIsNotNone(interval)
        self.assertLess(interval.lower, 1.0)
        self.assertGreater(interval.upper, interval.lower)

    def test_valid_wilson_interval(self):
        interval = wilson_confidence_interval(22, 25)
        self.assertIsNotNone(interval)
        self.assertEqual(interval.method, 'wilson')
        self.assertLess(interval.lower, 0.88)
        self.assertGreater(interval.upper, 0.88)
        self.assertLessEqual(interval.lower, interval.upper)

    def test_all_misses_have_valid_interval(self):
        interval = wilson_confidence_interval(0, 10)
        self.assertIsNotNone(interval)
        self.assertGreaterEqual(interval.lower, 0.0)
        self.assertLess(interval.upper, 0.30)


class PredictionPerformanceMetricTests(unittest.TestCase):
    def test_zero_settled_returns_null_win_rate(self):
        records = [performance_record(1, status=PredictionStatus.pending)]
        report = service().analyze_prediction_performance(records=records, now=NOW)

        self.assertEqual(report.prediction_pool.total_settled, 0)
        self.assertIsNone(report.prediction_pool.win_rate)
        self.assertIsNone(report.prediction_pool.confidence_interval)
        self.assertEqual(report.prediction_pool.non_settled_by_status, {'pending': 1})

    def test_all_wins(self):
        records = [
            performance_record(1, evidence_score=0.80),
            performance_record(2, evidence_score=0.90),
        ]
        report = service().analyze_prediction_performance(records=records, now=NOW)

        self.assertEqual(report.prediction_pool.total_settled, 2)
        self.assertEqual(report.prediction_pool.wins, 2)
        self.assertEqual(report.prediction_pool.misses, 0)
        self.assertEqual(report.prediction_pool.win_rate, 1.0)

    def test_all_misses(self):
        records = [
            performance_record(1, status=PredictionStatus.settled_miss),
            performance_record(2, status=PredictionStatus.settled_miss),
        ]
        report = service().analyze_prediction_performance(records=records, now=NOW)

        self.assertEqual(report.prediction_pool.wins, 0)
        self.assertEqual(report.prediction_pool.misses, 2)
        self.assertEqual(report.prediction_pool.win_rate, 0.0)

    def test_mixed_results_and_score_statistics(self):
        records = [
            performance_record(1, evidence_score=0.70, odds=1.40),
            performance_record(2, evidence_score=0.80, odds=1.44),
            performance_record(3, status=PredictionStatus.settled_miss, evidence_score=0.90, odds=1.49),
            performance_record(4, status=PredictionStatus.settled_miss, evidence_score=1.00, odds=1.49),
        ]
        report = service().analyze_prediction_performance(records=records, now=NOW)

        self.assertEqual(report.prediction_pool.total_settled, 4)
        self.assertEqual(report.prediction_pool.wins, 2)
        self.assertEqual(report.prediction_pool.misses, 2)
        self.assertEqual(report.prediction_pool.win_rate, 0.5)
        self.assertEqual(report.prediction_pool.average_evidence_score, 0.85)
        self.assertAlmostEqual(report.prediction_pool.median_evidence_score, 0.85)
        self.assertEqual(report.prediction_pool.evidence_score_sample_size, 4)
        self.assertEqual(report.prediction_pool.average_odds, (1.40 + 1.44 + 1.49 + 1.49) / 4)
        self.assertEqual(report.prediction_pool.odds_sample_size, 4)

    def test_non_results_are_excluded_from_win_rate(self):
        records = [
            performance_record(1),
            performance_record(2, status=PredictionStatus.postponed),
            performance_record(3, status=PredictionStatus.cancelled),
            performance_record(4, status=PredictionStatus.unresolved),
        ]
        report = service().analyze_prediction_performance(records=records, now=NOW)

        self.assertEqual(report.prediction_pool.total_settled, 1)
        self.assertEqual(report.prediction_pool.win_rate, 1.0)
        self.assertEqual(
            report.prediction_pool.non_settled_by_status,
            {'postponed': 1, 'cancelled': 1, 'unresolved': 1},
        )


class PredictionPerformanceGroupingTests(unittest.TestCase):
    def test_evidence_score_bands_use_correct_boundaries(self):
        records = [
            performance_record(1, evidence_score=0.69),
            performance_record(2, evidence_score=0.70),
            performance_record(3, evidence_score=0.749),
            performance_record(4, evidence_score=0.75),
            performance_record(5, evidence_score=0.849),
            performance_record(6, evidence_score=0.90),
            performance_record(7, evidence_score=None),
        ]
        report = service().analyze_prediction_performance(records=records, now=NOW)
        bands = {group.key: group for group in report.evidence_score_bands}

        self.assertEqual(bands['below_0_70'].total_settled, 1)
        self.assertEqual(bands['0_70_0_74'].total_settled, 2)
        self.assertEqual(bands['0_75_0_79'].total_settled, 1)
        self.assertEqual(bands['0_80_0_84'].total_settled, 1)
        self.assertEqual(bands['0_90_1_00'].total_settled, 1)
        self.assertEqual(bands['unavailable'].total_settled, 1)

    def test_odds_bands_use_target_boundaries(self):
        records = [
            performance_record(1, odds=1.39),
            performance_record(2, odds=1.40),
            performance_record(3, odds=1.44),
            performance_record(4, odds=1.45),
            performance_record(5, odds=1.49),
            performance_record(6, odds=1.50),
        ]
        report = service().analyze_prediction_performance(records=records, now=NOW)
        bands = {group.key: group for group in report.odds_bands}

        self.assertEqual(bands['outside_target_range'].total_settled, 2)
        self.assertEqual(bands['1_40_1_44'].total_settled, 2)
        self.assertEqual(bands['1_45_1_49'].total_settled, 2)

    def test_evidence_quality_groups_remain_separate(self):
        records = [
            performance_record(1, evidence_quality=EvidenceQuality.complete),
            performance_record(2, evidence_quality=EvidenceQuality.complete, status=PredictionStatus.settled_miss),
            performance_record(3, evidence_quality=EvidenceQuality.partial),
            performance_record(4, evidence_quality=EvidenceQuality.insufficient, status=PredictionStatus.settled_miss),
        ]
        report = service().analyze_prediction_performance(records=records, now=NOW)
        groups = {group.key: group for group in report.evidence_qualities}

        self.assertEqual(groups['complete'].wins, 1)
        self.assertEqual(groups['complete'].misses, 1)
        self.assertEqual(groups['partial'].wins, 1)
        self.assertEqual(groups['insufficient'].misses, 1)

    def test_model_versions_remain_separate(self):
        records = [
            performance_record(1, model_version='baseline-v1'),
            performance_record(2, model_version='evidence-v1'),
            performance_record(3, model_version='evidence-v1', status=PredictionStatus.settled_miss),
        ]
        report = service().analyze_prediction_performance(records=records, now=NOW)
        groups = {group.key: group for group in report.model_versions}

        self.assertEqual(groups['baseline-v1'].total_settled, 1)
        self.assertEqual(groups['evidence-v1'].wins, 1)
        self.assertEqual(groups['evidence-v1'].misses, 1)
        self.assertEqual(report.learning.current_model, 'evidence-v1')

    def test_date_and_competition_groups_expose_samples(self):
        records = [
            performance_record(1, kickoff=datetime(2026, 9, 12, 10, 0, tzinfo=UTC), competition='League One'),
            performance_record(2, kickoff=datetime(2026, 9, 13, 10, 0, tzinfo=UTC), competition='League One'),
            performance_record(3, kickoff=datetime(2026, 9, 14, 10, 0, tzinfo=UTC), competition='League Two'),
        ]
        report = service(minimum_group_sample=2).analyze_prediction_performance(
            records=records,
            now=NOW,
        )

        dates = {group.key: group for group in report.dates}
        competitions = {group.key: group for group in report.competitions}
        self.assertEqual(dates['2026-09-12'].sample_status.value, 'insufficient_sample')
        self.assertEqual(competitions['league one'].total_settled, 2)
        self.assertEqual(competitions['league two'].sample_status.value, 'insufficient_sample')

    def test_filters_are_applied_without_mutating_records(self):
        records = [
            performance_record(1, model_version='evidence-v1', evidence_quality=EvidenceQuality.complete, competition='League One'),
            performance_record(2, model_version='baseline-v1', evidence_quality=EvidenceQuality.partial, competition='League Two'),
        ]
        before = [record.model_dump(mode='json') for record in records]
        report = service().analyze_prediction_performance(
            records=records,
            model_version='evidence-v1',
            evidence_quality=EvidenceQuality.complete,
            competition='league one',
            start=datetime(2026, 9, 12, 0, 0, tzinfo=UTC),
            end=datetime(2026, 9, 14, 0, 0, tzinfo=UTC),
            now=NOW,
        )
        after = [record.model_dump(mode='json') for record in records]

        self.assertEqual(report.prediction_pool.total_records, 1)
        self.assertEqual(report.prediction_pool.total_settled, 1)
        self.assertEqual(before, after)


class PredictionProviderProbabilityTests(unittest.TestCase):
    def test_provider_probability_is_analyzed_separately_from_evidence_score(self):
        records = [
            performance_record(1, evidence_score=0.20, provider_probability=0.80),
            performance_record(2, status=PredictionStatus.settled_miss, evidence_score=0.90, provider_probability=0.70),
        ]
        report = service().analyze_prediction_performance(records=records, now=NOW)
        provider = report.provider_probability

        self.assertEqual(provider.probability_semantics, 'sportybet_provider_probability')
        self.assertEqual(provider.valid_sample_size, 2)
        self.assertEqual(provider.missing_sample_size, 0)
        self.assertEqual(provider.average_provider_probability, 0.75)
        self.assertAlmostEqual(provider.brier_score, ((0.8 - 1) ** 2 + (0.7 - 0) ** 2) / 2)
        self.assertEqual(report.prediction_pool.average_evidence_score, 0.55)

    def test_missing_provider_probability_is_not_replaced_by_inverse_odds(self):
        records = [
            performance_record(1, provider_probability=None, odds=1.43),
            performance_record(2, provider_probability=None, odds=1.47),
        ]
        report = service().analyze_prediction_performance(records=records, now=NOW)

        self.assertEqual(report.provider_probability.valid_sample_size, 0)
        self.assertEqual(report.provider_probability.missing_sample_size, 2)
        self.assertIsNone(report.provider_probability.average_provider_probability)
        self.assertIsNone(report.provider_probability.brier_score)
        self.assertAlmostEqual(report.prediction_pool.average_odds, 1.45)


class PredictionPoolComparisonTests(unittest.TestCase):
    def test_prediction_and_qualifying_populations_remain_separate(self):
        records = [
            performance_record(1, booking_pool='predictions'),
            performance_record(2, booking_pool='predictions', status=PredictionStatus.settled_miss),
            performance_record(3, booking_pool='qualifying'),
            performance_record(4, booking_pool='qualifying', status=PredictionStatus.settled_miss),
        ]
        report = service().analyze_prediction_performance(records=records, now=NOW)

        self.assertEqual(report.prediction_pool.total_settled, 2)
        self.assertEqual(report.prediction_pool.wins, 1)
        self.assertEqual(report.qualifying_pool.total_settled, 2)
        self.assertEqual(report.qualifying_pool.wins, 1)
        self.assertEqual(report.pool_comparison.observed_win_rate_difference, 0.0)
        self.assertFalse(report.pool_comparison.causal_claim)

    def test_qualifying_pool_is_absent_when_not_persisted(self):
        records = [performance_record(1)]
        report = service().analyze_prediction_performance(records=records, now=NOW)

        self.assertIsNone(report.qualifying_pool)
        self.assertEqual(report.pool_comparison.sample_status.value, 'insufficient_sample')
        self.assertIsNone(report.pool_comparison.observed_win_rate_difference)


class PredictionLearningTests(unittest.TestCase):
    def test_small_sample_does_not_generate_candidate_model(self):
        records = [
            performance_record(1),
            performance_record(2),
        ]
        analyzer = service(
            minimum_group_sample=3,
            minimum_analysis_sample=3,
            minimum_candidate_sample=5,
        )
        report = analyzer.analyze_prediction_performance(records=records, now=NOW)

        self.assertEqual(report.learning.status.value, 'insufficient_sample')
        self.assertEqual(report.learning.settled, 2)
        self.assertIsNone(report.learning.candidate_model)
        self.assertFalse(report.learning.active_model_changed)

    def test_candidate_ready_sample_still_does_not_activate_model(self):
        records = [performance_record(index) for index in range(1, 6)]
        report = service(
            minimum_analysis_sample=3,
            minimum_candidate_sample=5,
        ).analyze_prediction_performance(records=records, now=NOW)

        self.assertEqual(report.learning.status.value, 'candidate_evaluation_ready')
        self.assertEqual(report.learning.settled, 5)
        self.assertIsNone(report.learning.candidate_model)
        self.assertFalse(report.learning.active_model_changed)
        self.assertEqual(report.learning.current_model, 'evidence-v1')

    def test_temporal_split_is_chronological(self):
        records = [
            performance_record(
                index,
                kickoff=NOW + timedelta(days=index),
            )
            for index in range(1, 11)
        ]
        report = service(
            minimum_analysis_sample=3,
            minimum_candidate_sample=10,
        ).analyze_prediction_performance(records=records, now=NOW)
        split = report.learning.temporal_split

        self.assertIsNotNone(split)
        self.assertEqual(split.training_settled, 6)
        self.assertEqual(split.validation_settled, 2)
        self.assertEqual(split.holdout_settled, 2)
        self.assertLess(split.training_end, split.validation_start)
        self.assertLess(split.validation_end, split.holdout_start)

    def test_analysis_is_read_only_and_does_not_call_store(self):
        store = mock.Mock()
        records = [performance_record(1), performance_record(2)]
        before = [record.model_dump(mode='json') for record in records]
        analyzer = PredictionPerformanceService(store)
        report = analyzer.analyze_prediction_performance(records=records, now=NOW)
        after = [record.model_dump(mode='json') for record in records]

        self.assertEqual(report.prediction_pool.total_settled, 2)
        self.assertEqual(before, after)
        store.list_predictions.assert_not_called()


if __name__ == '__main__':
    unittest.main()
