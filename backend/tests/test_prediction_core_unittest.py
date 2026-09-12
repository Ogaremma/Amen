import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from app.schemas.sportybet_markets import (
    OverOneHalfCandidate,
    SportyBetFixtureWithMarkets,
    SportyBetMarket,
    SportyBetOutcome,
    SportyBetSelectionIdentity,
)
from app.services.prediction_engine import (
    BASELINE_MIN_SCORE,
    evaluate_prediction,
)
from app.services.prediction_features import extract_prediction_features
from app.services.prediction_store import PredictionStore
from app.services.prediction_windows import in_today_tomorrow_window, today_tomorrow_window
from app.services.sportybet_markets import extract_over_one_half_candidates


UTC = timezone.utc
REFERENCE_NOW = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)


def make_candidate(
    *,
    odds=1.43,
    active=True,
    kickoff=None,
    event_id='sr:match:1',
    market_id='18',
    specifier='total=1.5',
    outcome_id='12',
    product_id=3,
    label='Over 1.5',
    sport_id='sr:sport:1',
    home_team='Home',
    away_team='Away',
    competition='League',
    probability=None,
    market_banned=False,
    fixture_banned=False,
):
    kickoff = kickoff or datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
    outcome = SportyBetOutcome(
        outcome_id=outcome_id,
        description=label,
        odds=odds,
        probability=probability,
        is_active=active,
    )
    market = SportyBetMarket(
        market_id=market_id,
        specifier=specifier,
        product_id=product_id,
        description='Over/Under',
        name='Over/Under',
        status=0,
        is_banned=market_banned,
        source_type='prematch',
        outcomes=[outcome],
    )
    fixture = SportyBetFixtureWithMarkets(
        event_id=event_id,
        sport_id=sport_id,
        sport_name='Football',
        category_id='sr:category:1',
        category_name='England',
        tournament_id='sr:tournament:17',
        tournament_name='Premier League',
        competition=competition,
        home_team_name=home_team,
        away_team_name=away_team,
        kickoff=kickoff,
        status=0,
        match_status='prematch',
        is_banned=fixture_banned,
        markets=[market],
    )
    identity = SportyBetSelectionIdentity(
        event_id=event_id,
        market_id=market_id,
        outcome_id=outcome_id,
        product_id=product_id,
        sport_id=sport_id,
        specifier=specifier,
    )
    return OverOneHalfCandidate(identity=identity, fixture=fixture, market=market, outcome=outcome)


class PredictionEngineTests(unittest.TestCase):
    def test_strong_candidate_is_selected(self):
        candidate = make_candidate(odds=1.43)
        evaluation = evaluate_prediction(candidate)
        self.assertTrue(evaluation.selected)
        self.assertGreaterEqual(evaluation.model_score, BASELINE_MIN_SCORE)
        self.assertEqual(evaluation.confidence_label, 'Strong candidate')

    def test_candidate_below_threshold_is_not_selected(self):
        candidate = make_candidate(odds=1.49)
        evaluation = evaluate_prediction(candidate)
        self.assertFalse(evaluation.selected)
        self.assertLess(evaluation.model_score, BASELINE_MIN_SCORE)

    def test_odds_1_40_is_included(self):
        candidate = make_candidate(odds=1.40)
        evaluation = evaluate_prediction(candidate)
        self.assertTrue(evaluation.selected)
        self.assertEqual(evaluation.odds, 1.40)

    def test_odds_1_49_is_included_in_qualifying_pool(self):
        candidate = make_candidate(odds=1.49)
        evaluation = evaluate_prediction(candidate)
        self.assertEqual(evaluation.odds, 1.49)
        self.assertTrue(evaluation.feature_snapshot.data_quality.target_odds_range)

    def test_odds_1_50_is_excluded(self):
        candidate = make_candidate(odds=1.50)
        extracted = extract_over_one_half_candidates([candidate.fixture])
        self.assertEqual(extracted, [])

    def test_odds_below_1_40_is_excluded(self):
        candidate = make_candidate(odds=1.39)
        extracted = extract_over_one_half_candidates([candidate.fixture])
        self.assertEqual(extracted, [])

    def test_inactive_outcome_is_excluded(self):
        candidate = make_candidate(active=False)
        evaluation = evaluate_prediction(candidate)
        self.assertFalse(evaluation.selected)
        self.assertFalse(evaluation.feature_snapshot.data_quality.active_outcome)

    def test_invalid_odds_are_excluded(self):
        candidate = make_candidate(odds=None)
        evaluation = evaluate_prediction(candidate)
        self.assertFalse(evaluation.selected)
        self.assertFalse(evaluation.feature_snapshot.data_quality.valid_odds)

    def test_missing_fixture_data_is_rejected(self):
        candidate = make_candidate(sport_id='')
        evaluation = evaluate_prediction(candidate)
        self.assertFalse(evaluation.selected)
        self.assertFalse(evaluation.feature_snapshot.data_quality.required_fields_present)

    def test_wrong_market_is_rejected(self):
        candidate = make_candidate(market_id='19')
        evaluation = evaluate_prediction(candidate)
        self.assertFalse(evaluation.selected)
        self.assertFalse(evaluation.feature_snapshot.data_quality.market_identity_verified)

    def test_wrong_specifier_is_rejected(self):
        candidate = make_candidate(specifier='total=2.5')
        evaluation = evaluate_prediction(candidate)
        self.assertFalse(evaluation.selected)
        self.assertFalse(evaluation.feature_snapshot.data_quality.market_identity_verified)

    def test_wrong_outcome_is_rejected(self):
        candidate = make_candidate(outcome_id='13', label='Under 1.5')
        evaluation = evaluate_prediction(candidate)
        self.assertFalse(evaluation.selected)
        self.assertFalse(evaluation.feature_snapshot.data_quality.market_identity_verified)

    def test_wrong_product_is_rejected(self):
        candidate = make_candidate(product_id=4)
        evaluation = evaluate_prediction(candidate)
        self.assertFalse(evaluation.selected)
        self.assertFalse(evaluation.feature_snapshot.data_quality.market_identity_verified)

    def test_exact_selection_identity_is_preserved(self):
        candidate = make_candidate()
        evaluation = evaluate_prediction(candidate)
        self.assertEqual(
            evaluation.identity.model_dump(),
            candidate.identity.model_dump(),
        )

    def test_score_is_deterministic(self):
        candidate = make_candidate(odds=1.45, probability=0.7)
        first = evaluate_prediction(candidate)
        second = evaluate_prediction(candidate)
        self.assertEqual(first.model_score, second.model_score)
        self.assertEqual(first.selection_reason, second.selection_reason)

    def test_explanation_does_not_use_certainty_language(self):
        candidate = make_candidate(odds=1.43)
        evaluation = evaluate_prediction(candidate)
        forbidden = {'guaranteed', 'certain', '100%', 'sure win'}
        self.assertFalse(any(word in evaluation.selection_reason.lower() for word in forbidden))
        self.assertIn('1.43', evaluation.selection_reason)


class PredictionWindowTests(unittest.TestCase):
    def test_same_day_future_fixture_is_included(self):
        kickoff = REFERENCE_NOW + timedelta(hours=1)
        self.assertTrue(in_today_tomorrow_window(kickoff, REFERENCE_NOW))

    def test_tomorrow_fixture_is_included(self):
        kickoff = REFERENCE_NOW + timedelta(days=1)
        self.assertTrue(in_today_tomorrow_window(kickoff, REFERENCE_NOW))

    def test_yesterday_fixture_is_excluded(self):
        kickoff = REFERENCE_NOW - timedelta(days=1)
        self.assertFalse(in_today_tomorrow_window(kickoff, REFERENCE_NOW))

    def test_already_started_fixture_is_excluded(self):
        kickoff = REFERENCE_NOW - timedelta(minutes=1)
        self.assertFalse(in_today_tomorrow_window(kickoff, REFERENCE_NOW))

    def test_missing_kickoff_is_excluded(self):
        self.assertFalse(in_today_tomorrow_window(None, REFERENCE_NOW))

    def test_midnight_boundary_is_inclusive_at_start_and_exclusive_at_end(self):
        now = datetime(2026, 9, 12, 0, 0, tzinfo=UTC)
        window = today_tomorrow_window(now)
        self.assertEqual(window.start, now)
        self.assertEqual(window.end_exclusive, datetime(2026, 9, 14, 0, 0, tzinfo=UTC))
        self.assertTrue(in_today_tomorrow_window(now, now))
        self.assertTrue(in_today_tomorrow_window(datetime(2026, 9, 13, 23, 59, tzinfo=UTC), now))
        self.assertFalse(in_today_tomorrow_window(datetime(2026, 9, 14, 0, 0, tzinfo=UTC), now))

    def test_timezone_offsets_are_normalized_to_utc(self):
        kickoff = datetime(2026, 9, 12, 15, 0, tzinfo=timezone(timedelta(hours=2)))
        self.assertTrue(in_today_tomorrow_window(kickoff, REFERENCE_NOW))


class PredictionStoreTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix='.sqlite3')
        os.close(handle)
        self.store = PredictionStore(self.path)

    def tearDown(self):
        os.unlink(self.path)

    def test_selected_prediction_is_persisted_with_structured_feature_snapshot(self):
        candidate = make_candidate(odds=1.43, probability=0.68)
        evaluation = evaluate_prediction(candidate)
        result = self.store.save_predictions([evaluation])
        self.assertEqual(result.inserted, 1)
        records = self.store.list_predictions()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].feature_snapshot, evaluation.feature_snapshot)
        self.assertEqual(records[0].prediction_status.value, 'pending')
        self.assertIsNone(records[0].actual_goals)
        self.assertIsNone(records[0].actual_result)
        self.assertIsNone(records[0].settled_at)

    def test_duplicate_selection_identity_is_not_duplicated(self):
        evaluation = evaluate_prediction(make_candidate())
        first = self.store.save_predictions([evaluation])
        second = self.store.save_predictions([evaluation])
        self.assertEqual(first.inserted, 1)
        self.assertEqual(second.inserted, 0)
        self.assertEqual(second.skipped_existing, 1)
        self.assertEqual(len(self.store.list_predictions()), 1)

    def test_deduplication_uses_full_identity_not_team_names(self):
        first = evaluate_prediction(make_candidate(event_id='sr:match:1'))
        second = evaluate_prediction(make_candidate(event_id='sr:match:2'))
        result = self.store.save_predictions([first, second])
        self.assertEqual(result.inserted, 2)
        self.assertEqual(len(self.store.list_predictions()), 2)

    def test_unselected_predictions_are_ignored(self):
        evaluation = evaluate_prediction(make_candidate(odds=1.49))
        result = self.store.save_predictions([evaluation])
        self.assertEqual(result.inserted, 0)
        self.assertEqual(result.ignored_not_selected, 1)
        self.assertEqual(self.store.list_predictions(), [])

    def test_prediction_can_be_retrieved_by_identity(self):
        candidate = make_candidate()
        evaluation = evaluate_prediction(candidate)
        self.store.save_predictions([evaluation])
        record = self.store.get_prediction(candidate.identity)
        self.assertIsNotNone(record)
        self.assertEqual(record.event_id, candidate.identity.event_id)
        self.assertEqual(record.market_id, candidate.identity.market_id)
        self.assertEqual(record.outcome_id, candidate.identity.outcome_id)
        self.assertEqual(record.product_id, candidate.identity.product_id)
        self.assertEqual(record.sport_id, candidate.identity.sport_id)
        self.assertEqual(record.specifier, candidate.identity.specifier)
