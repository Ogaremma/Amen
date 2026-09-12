import copy
import json
import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from app.schemas.prediction import EvidenceQuality
from app.services.prediction_engine import evaluate_prediction
from app.services.prediction_evidence import CapturedFootballEvidenceProvider
from app.services.prediction_evidence_engine import (
    EVIDENCE_MIN_SCORE,
    EVIDENCE_MODEL_VERSION,
    evaluate_evidence_prediction,
    evaluate_evidence_predictions,
)
from app.services.prediction_store import PredictionStore
from backend.tests.test_prediction_evidence_unittest import NOW, make_candidate


FIXTURES = Path(__file__).parent / 'fixtures'


def load_evidence():
    fixture = json.loads(
        (FIXTURES / 'prediction_evidence_aston_villa_nottingham.json').read_text(
            encoding='utf-8'
        )
    )
    season = json.loads(
        (FIXTURES / 'prediction_evidence_season_stats.json').read_text(encoding='utf-8')
    )
    provider = CapturedFootballEvidenceProvider(
        match_info_payload=fixture['match_info'],
        home_team_lastx_payload=fixture['home_team_lastx'],
        away_team_lastx_payload=fixture['away_team_lastx'],
        season_unique_team_stats_payload=season,
    )
    return provider.get_evidence(make_candidate(), now=NOW)


def candidate_with_probability(value: float | None):
    candidate = make_candidate()
    candidate.outcome.probability = value
    candidate.market.outcomes[0].probability = value
    return candidate


def set_form_evidence(
    evidence,
    *,
    over_rate: float,
    sample_size: int,
    goals_scored: float,
    goals_conceded: float,
):
    result = evidence.model_copy(deep=True)
    for form in (result.home_form, result.away_form):
        for feature in (form.over_1_5_rate_last_5, form.over_1_5_rate_last_10):
            feature.value = over_rate
            feature.sample_size = sample_size
            feature.valid = True
        for feature in (
            form.goals_scored_average_last_5,
            form.goals_scored_average_last_10,
        ):
            feature.value = goals_scored
            feature.sample_size = sample_size
            feature.valid = True
        for feature in (
            form.goals_conceded_average_last_5,
            form.goals_conceded_average_last_10,
        ):
            feature.value = goals_conceded
            feature.sample_size = sample_size
            feature.valid = True
    return result


def complete_evidence(evidence):
    result = set_form_evidence(
        evidence,
        over_rate=0.80,
        sample_size=10,
        goals_scored=1.40,
        goals_conceded=1.10,
    )
    for form, venue_rate in (
        (result.home_form.home, 0.80),
        (result.away_form.away, 0.80),
    ):
        venue_rate_feature = venue_rate
        form.over_1_5_rate.value = venue_rate_feature
        form.over_1_5_rate.sample_size = 5
        form.over_1_5_rate.valid = True
    return result


def weak_evidence(evidence):
    result = set_form_evidence(
        evidence,
        over_rate=0.20,
        sample_size=10,
        goals_scored=0.50,
        goals_conceded=0.50,
    )
    for form in (result.home_form.home, result.away_form.away):
        form.over_1_5_rate.value = 0.20
        form.over_1_5_rate.sample_size = 5
        form.over_1_5_rate.valid = True
    return result


class EvidenceModelMarketTests(unittest.TestCase):
    def setUp(self):
        self.evidence = load_evidence()

    def test_valid_target_market_scores_and_selects_with_complete_evidence(self):
        evaluation = evaluate_evidence_prediction(
            make_candidate(),
            football_evidence=complete_evidence(self.evidence),
            now=NOW,
        )
        self.assertEqual(evaluation.evidence_score, 0.8313)
        self.assertEqual(evaluation.evidence_quality, EvidenceQuality.complete)
        self.assertTrue(evaluation.selected)
        self.assertEqual(evaluation.evidence_score_breakdown.market_quality, 0.2975)

    def test_odds_outside_target_range_are_excluded(self):
        candidate = make_candidate()
        candidate.outcome.odds = 1.50
        candidate.market.outcomes[0].odds = 1.50
        evaluation = evaluate_evidence_prediction(
            candidate,
            football_evidence=complete_evidence(self.evidence),
            now=NOW,
        )
        self.assertFalse(evaluation.selected)
        self.assertEqual(evaluation.evidence_score_breakdown.market_quality, 0.0)

    def test_inactive_market_is_excluded(self):
        candidate = make_candidate()
        candidate.outcome.is_active = False
        candidate.market.outcomes[0].is_active = False
        evaluation = evaluate_evidence_prediction(
            candidate,
            football_evidence=complete_evidence(self.evidence),
            now=NOW,
        )
        self.assertFalse(evaluation.selected)
        self.assertEqual(evaluation.evidence_score_breakdown.market_quality, 0.0)

    def test_invalid_odds_are_excluded(self):
        candidate = make_candidate()
        candidate.outcome.odds = None
        candidate.market.outcomes[0].odds = None
        evaluation = evaluate_evidence_prediction(
            candidate,
            football_evidence=complete_evidence(self.evidence),
            now=NOW,
        )
        self.assertFalse(evaluation.selected)
        self.assertEqual(evaluation.evidence_score_breakdown.market_quality, 0.0)


class EvidenceModelProviderProbabilityTests(unittest.TestCase):
    def setUp(self):
        self.evidence = load_evidence()

    def test_valid_provider_probability_is_used_and_labeled(self):
        evaluation = evaluate_evidence_prediction(
            make_candidate(),
            football_evidence=complete_evidence(self.evidence),
            now=NOW,
        )
        self.assertEqual(evaluation.provider_probability, 0.691803)
        self.assertEqual(evaluation.provider_probability_source, 0.691803)
        self.assertEqual(
            evaluation.evidence_score_breakdown.provider_probability,
            0.1038,
        )
        self.assertIn('not as an Amen-calibrated probability', ' '.join(evaluation.reasons))

    def test_missing_provider_probability_is_not_invented(self):
        evaluation = evaluate_evidence_prediction(
            candidate_with_probability(None),
            football_evidence=complete_evidence(self.evidence),
            now=NOW,
        )
        self.assertIsNone(evaluation.provider_probability)
        self.assertEqual(evaluation.evidence_score_breakdown.provider_probability, 0.0)
        self.assertIn(
            'SportyBet provider probability is unavailable.',
            evaluation.explanation.missing_signals,
        )

    def test_invalid_provider_probability_is_rejected_but_source_is_preserved(self):
        evaluation = evaluate_evidence_prediction(
            candidate_with_probability(1.20),
            football_evidence=complete_evidence(self.evidence),
            now=NOW,
        )
        self.assertIsNone(evaluation.provider_probability)
        self.assertEqual(evaluation.provider_probability_source, 1.20)
        self.assertEqual(evaluation.evidence_score_breakdown.provider_probability, 0.0)
        self.assertIn(
            'The supplied SportyBet probability 1.2000 is invalid and is not used.',
            evaluation.explanation.negative_signals,
        )


class EvidenceModelFootballEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.evidence = load_evidence()

    def test_strong_over_1_5_evidence_outscores_weak_evidence(self):
        strong = evaluate_evidence_prediction(
            make_candidate(),
            football_evidence=complete_evidence(self.evidence),
            now=NOW,
        )
        weak = evaluate_evidence_prediction(
            make_candidate(),
            football_evidence=weak_evidence(self.evidence),
            now=NOW,
        )
        self.assertGreater(strong.evidence_score, weak.evidence_score)
        self.assertEqual(strong.evidence_score_breakdown.historical_over_1_5, 0.2)
        self.assertEqual(weak.evidence_score_breakdown.historical_over_1_5, 0.05)

    def test_larger_sample_size_receives_more_reliability(self):
        small = evaluate_evidence_prediction(
            make_candidate(),
            football_evidence=self.evidence,
            now=NOW,
        )
        larger = set_form_evidence(
            self.evidence,
            over_rate=0.50,
            sample_size=10,
            goals_scored=1.00,
            goals_conceded=1.00,
        )
        larger_evaluation = evaluate_evidence_prediction(
            make_candidate(),
            football_evidence=larger,
            now=NOW,
        )
        self.assertEqual(small.evidence_quality, EvidenceQuality.partial)
        self.assertEqual(larger_evaluation.evidence_quality, EvidenceQuality.complete)
        self.assertLess(
            small.evidence_score_breakdown.historical_over_1_5,
            larger_evaluation.evidence_score_breakdown.historical_over_1_5,
        )

    def test_missing_history_is_insufficient(self):
        evidence = self.evidence.model_copy(deep=True)
        evidence.home_form = None
        evaluation = evaluate_evidence_prediction(
            make_candidate(),
            football_evidence=evidence,
            now=NOW,
        )
        self.assertEqual(evaluation.evidence_quality, EvidenceQuality.insufficient)
        self.assertFalse(evaluation.selected)
        self.assertIn(
            'Home-team recent form is unavailable.',
            evaluation.explanation.missing_signals,
        )

    def test_invalid_feature_rows_do_not_contribute(self):
        evidence = self.evidence.model_copy(deep=True)
        for form in (evidence.home_form, evidence.away_form):
            form.over_1_5_rate_last_5.valid = False
            form.over_1_5_rate_last_10.valid = False
            form.goals_scored_average_last_5.valid = False
            form.goals_scored_average_last_10.valid = False
        evaluation = evaluate_evidence_prediction(
            make_candidate(),
            football_evidence=evidence,
            now=NOW,
        )
        self.assertEqual(evaluation.evidence_quality, EvidenceQuality.insufficient)
        self.assertEqual(evaluation.evidence_score_breakdown.historical_over_1_5, 0.0)
        self.assertEqual(evaluation.evidence_score_breakdown.goal_production, 0.0)


class EvidenceModelQualityAndSafetyTests(unittest.TestCase):
    def setUp(self):
        self.evidence = load_evidence()

    def test_evidence_quality_levels_are_deterministic(self):
        complete = evaluate_evidence_prediction(
            make_candidate(),
            football_evidence=complete_evidence(self.evidence),
            now=NOW,
        )
        partial = evaluate_evidence_prediction(
            make_candidate(),
            football_evidence=self.evidence,
            now=NOW,
        )
        insufficient = evaluate_evidence_prediction(make_candidate(), now=NOW)
        self.assertEqual(complete.evidence_quality, EvidenceQuality.complete)
        self.assertEqual(partial.evidence_quality, EvidenceQuality.partial)
        self.assertEqual(insufficient.evidence_quality, EvidenceQuality.insufficient)

    def test_conflicting_home_and_away_evidence_is_explained(self):
        evidence = complete_evidence(self.evidence)
        evidence.away_form.over_1_5_rate_last_5.value = 0.20
        evidence.away_form.over_1_5_rate_last_10.value = 0.20
        evaluation = evaluate_evidence_prediction(
            make_candidate(),
            football_evidence=evidence,
            now=NOW,
        )
        self.assertIn(
            'Home and away recent Over 1.5 evidence conflicts.',
            evaluation.explanation.negative_signals,
        )

    def test_scores_are_normalized_for_all_supported_inputs(self):
        evidence_variants = [
            None,
            self.evidence,
            complete_evidence(self.evidence),
            weak_evidence(self.evidence),
        ]
        for evidence in evidence_variants:
            evaluation = evaluate_evidence_prediction(
                make_candidate(),
                football_evidence=evidence,
                now=NOW,
            )
            self.assertGreaterEqual(evaluation.evidence_score, 0.0)
            self.assertLessEqual(evaluation.evidence_score, 1.0)
            self.assertGreaterEqual(evaluation.model_score, 0.0)
            self.assertLessEqual(evaluation.model_score, 1.0)

    def test_same_candidate_and_evidence_produce_identical_results(self):
        first = evaluate_evidence_prediction(
            make_candidate(),
            football_evidence=complete_evidence(self.evidence),
            now=NOW,
        )
        second = evaluate_evidence_prediction(
            make_candidate(),
            football_evidence=complete_evidence(self.evidence),
            now=NOW,
        )
        self.assertEqual(first, second)

    def test_future_result_is_not_used_and_is_reported(self):
        evidence = complete_evidence(self.evidence)
        future_match = copy.deepcopy(evidence.home_form.last_10[0])
        future_match.kickoff_at = evidence.context.source.retrieved_at + timedelta(days=30)
        evidence.home_form.last_10.append(future_match)
        evaluation = evaluate_evidence_prediction(
            make_candidate(),
            football_evidence=evidence,
            now=NOW,
        )
        self.assertEqual(evaluation.evidence_quality, EvidenceQuality.insufficient)
        self.assertFalse(evaluation.selected)
        self.assertTrue(
            any(
                'at or after prediction kickoff' in signal
                for signal in evaluation.explanation.negative_signals
            )
        )

    def test_stale_evidence_is_not_used(self):
        evaluation = evaluate_evidence_prediction(
            make_candidate(),
            football_evidence=complete_evidence(self.evidence),
            now=NOW + timedelta(seconds=3601),
        )
        self.assertEqual(evaluation.evidence_quality, EvidenceQuality.insufficient)
        self.assertEqual(evaluation.evidence_score_breakdown.historical_over_1_5, 0.0)

    def test_outcome_fields_do_not_enter_prediction_scoring(self):
        candidate = make_candidate()
        evidence = complete_evidence(self.evidence)
        first = evaluate_evidence_prediction(
            candidate,
            football_evidence=evidence,
            now=NOW,
        )
        second = evaluate_evidence_prediction(
            candidate,
            football_evidence=evidence,
            now=NOW,
        )
        self.assertEqual(first, second)
        self.assertNotIn('actual_result', type(first.feature_snapshot).model_fields)
        self.assertNotIn('actual_goals', type(first.feature_snapshot).model_fields)

    def test_baseline_and_evidence_model_versions_remain_distinguishable(self):
        baseline = evaluate_prediction(make_candidate())
        evidence = evaluate_evidence_prediction(
            make_candidate(),
            football_evidence=complete_evidence(self.evidence),
            now=NOW,
        )
        self.assertEqual(baseline.model_version, 'baseline-v1')
        self.assertEqual(evidence.model_version, EVIDENCE_MODEL_VERSION)
        self.assertEqual(evidence.baseline_score, baseline.model_score)
        self.assertIsNotNone(evidence.evidence_score)
        self.assertIsNone(baseline.evidence_score)

    def test_batch_evaluation_preserves_each_model_result(self):
        evaluations = evaluate_evidence_predictions(
            [make_candidate()],
            football_evidence_by_event_id={
                'sr:match:72221252': complete_evidence(self.evidence)
            },
            now=NOW,
        )
        self.assertEqual(len(evaluations), 1)
        self.assertEqual(evaluations[0].model_version, EVIDENCE_MODEL_VERSION)
        self.assertEqual(evaluations[0].evidence_score, 0.8313)


class EvidenceModelPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.evidence = complete_evidence(load_evidence())

    def test_evidence_model_result_is_persisted_and_retrieved(self):
        handle, path = tempfile.mkstemp(suffix='.sqlite3')
        os.close(handle)
        store = PredictionStore(path)
        try:
            evaluation = evaluate_evidence_prediction(
                make_candidate(),
                football_evidence=self.evidence,
                now=NOW,
            )
            result = store.save_predictions([evaluation])
            self.assertEqual(result.inserted, 1)
            record = store.get_prediction(evaluation.identity)
            self.assertEqual(record.model_version, EVIDENCE_MODEL_VERSION)
            self.assertEqual(record.baseline_score, evaluation.baseline_score)
            self.assertEqual(record.evidence_score, evaluation.evidence_score)
            self.assertEqual(record.evidence_quality, EvidenceQuality.complete)
            self.assertEqual(record.provider_probability, 0.691803)
            self.assertEqual(record.provider_probability_source, 0.691803)
            self.assertEqual(record.explanation, evaluation.explanation)
            self.assertEqual(record.feature_snapshot, evaluation.feature_snapshot)
        finally:
            os.unlink(path)

    def test_venue_specific_evidence_is_used(self):
        evaluation = evaluate_evidence_prediction(
            make_candidate(),
            football_evidence=complete_evidence(self.evidence),
            now=NOW,
        )
        self.assertEqual(evaluation.evidence_score_breakdown.venue_context, 0.08)
        self.assertTrue(
            any(
                'venue-specific' in signal
                for signal in evaluation.explanation.positive_signals
            )
        )

    def test_missing_venue_split_is_reported(self):
        evidence = complete_evidence(self.evidence)
        evidence.home_form.home = None
        evaluation = evaluate_evidence_prediction(
            make_candidate(),
            football_evidence=evidence,
            now=NOW,
        )
        self.assertIn(
            'Venue-specific home/away splits are unavailable.',
            evaluation.explanation.missing_signals,
        )
        self.assertEqual(evaluation.evidence_quality, EvidenceQuality.partial)

    def test_zero_goal_samples_do_not_divide_by_zero(self):
        evidence = self.evidence.model_copy(deep=True)
        for form in (evidence.home_form, evidence.away_form):
            form.season_stats = None
            for feature in (
                form.goals_scored_average_last_5,
                form.goals_scored_average_last_10,
                form.goals_conceded_average_last_5,
                form.goals_conceded_average_last_10,
            ):
                feature.value = None
                feature.sample_size = 0
                feature.valid = False
        evaluation = evaluate_evidence_prediction(
            make_candidate(),
            football_evidence=evidence,
            now=NOW,
        )
        self.assertEqual(evaluation.evidence_score_breakdown.goal_production, 0.0)
