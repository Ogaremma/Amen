import copy
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.schemas.sportybet_markets import (
    SportyBetFixtureWithMarkets,
    SportyBetMarket,
    SportyBetOutcome,
)
from app.services.prediction_engine import evaluate_prediction
from app.services.prediction_evidence import (
    CapturedFootballEvidenceProvider,
    build_football_evidence,
    collect_football_evidence,
    find_matching_match_info,
    is_evidence_fresh,
)
from app.services.prediction_features import extract_prediction_features
from app.services.prediction_store import PredictionStore
from app.services.sportybet_markets import extract_over_one_half_candidates


FIXTURES = Path(__file__).parent / 'fixtures'
EVIDENCE_FIXTURE = FIXTURES / 'prediction_evidence_aston_villa_nottingham.json'
SEASON_FIXTURE = FIXTURES / 'prediction_evidence_season_stats.json'
CAPTURED_AT = datetime.fromtimestamp(1789137800, timezone.utc)
NOW = datetime.fromtimestamp(1789138000, timezone.utc)


def make_candidate(
    event_id: str = 'sr:match:72221252',
    home_team_id: str | None = 'sr:competitor:40',
    away_team_id: str | None = 'sr:competitor:14',
    home_team_name: str = 'Aston Villa',
    away_team_name: str = 'Nottingham Forest',
):
    fixture = SportyBetFixtureWithMarkets(
        event_id=event_id,
        game_id='40427',
        sport_id='sr:sport:1',
        sport_name='Football',
        category_id='sr:category:1',
        category_name='England',
        tournament_id='sr:tournament:17',
        tournament_name='Premier League',
        competition='Premier League',
        home_team_id=home_team_id,
        home_team_name=home_team_name,
        away_team_id=away_team_id,
        away_team_name=away_team_name,
        kickoff=datetime.fromtimestamp(1789221600, timezone.utc),
        status=0,
        match_status='Not start',
        is_banned=False,
        markets=[
            SportyBetMarket(
                market_id='18',
                specifier='total=1.5',
                product_id=3,
                description='Over/Under',
                name='Over/Under',
                status=0,
                is_banned=False,
                source_type='BET_RADAR',
                outcomes=[
                    SportyBetOutcome(
                        outcome_id='12',
                        description='Over 1.5',
                        odds=1.45,
                        probability=0.691803,
                        is_active=True,
                    ),
                    SportyBetOutcome(
                        outcome_id='13',
                        description='Under 1.5',
                        odds=2.70,
                        probability=0.308197,
                        is_active=True,
                    ),
                ],
            )
        ],
    )
    return extract_over_one_half_candidates([fixture])[0]


def load_fixture():
    return json.loads(EVIDENCE_FIXTURE.read_text(encoding='utf-8'))


def load_season_fixture():
    return json.loads(SEASON_FIXTURE.read_text(encoding='utf-8'))


class FootballEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = load_fixture()
        self.season = load_season_fixture()
        self.candidate = make_candidate()
        self.provider = CapturedFootballEvidenceProvider(
            match_info_payload=self.fixture['match_info'],
            home_team_lastx_payload=self.fixture['home_team_lastx'],
            away_team_lastx_payload=self.fixture['away_team_lastx'],
            season_unique_team_stats_payload=self.season,
        )
        self.evidence = self.provider.get_evidence(self.candidate, now=NOW)
        self.assertIsNotNone(self.evidence)

    def test_successful_evidence_retrieval_preserves_identity_and_source(self):
        context = self.evidence.context
        self.assertEqual(context.sportybet_event_id, 'sr:match:72221252')
        self.assertEqual(context.sportybet_source_id, '72221252')
        self.assertEqual(context.match_id, '72221252')
        self.assertEqual(context.home_team_id, '40')
        self.assertEqual(context.away_team_id, '14')
        self.assertEqual(context.competition, 'Premier League')
        self.assertEqual(context.season_id, '140756')
        self.assertEqual(context.source.source, 'sportradar-public-widget-capture')
        self.assertEqual(context.source.feed, 'match_info')
        self.assertEqual(context.source.max_age_seconds, 3600)

    def test_recent_form_calculations_preserve_sample_sizes(self):
        home = self.evidence.home_form
        away = self.evidence.away_form
        self.assertEqual(home.over_1_5_rate_last_5.value, 0.3333)
        self.assertEqual(home.over_1_5_rate_last_5.sample_size, 3)
        self.assertEqual(home.goals_scored_average_last_5.value, 1.0)
        self.assertEqual(home.goals_conceded_average_last_5.value, 1.0)
        self.assertEqual(away.over_1_5_rate_last_5.value, 0.6667)
        self.assertEqual(away.over_1_5_rate_last_5.sample_size, 3)
        self.assertEqual(away.goals_scored_average_last_5.value, 0.6667)
        self.assertEqual(away.goals_conceded_average_last_5.value, 1.3333)

    def test_home_away_splits_are_filtered_and_sampled(self):
        home = self.evidence.home_form
        away = self.evidence.away_form
        self.assertEqual(home.home.over_1_5_rate.sample_size, 1)
        self.assertEqual(home.away.over_1_5_rate.sample_size, 2)
        self.assertEqual(away.home.over_1_5_rate.sample_size, 2)
        self.assertEqual(away.away.over_1_5_rate.sample_size, 1)
        self.assertEqual(home.home.goals_scored_average.value, 0.0)
        self.assertEqual(home.away.goals_scored_average.value, 1.5)

    def test_duplicate_historical_matches_are_deduplicated(self):
        fixture = copy.deepcopy(self.fixture)
        duplicate = copy.deepcopy(fixture['home_team_lastx']['doc'][0]['data']['matches'][0])
        duplicate['result'] = {'home': 9, 'away': 9}
        fixture['home_team_lastx']['doc'][0]['data']['matches'].append(duplicate)
        evidence = build_football_evidence(
            self.candidate,
            match_info_payload=fixture['match_info'],
            home_team_lastx_payload=fixture['home_team_lastx'],
            away_team_lastx_payload=fixture['away_team_lastx'],
        )
        self.assertEqual(len(evidence.home_form.last_10), 3)
        self.assertEqual(evidence.home_form.last_10[0].goals_for, 3)

    def test_invalid_match_scores_are_skipped(self):
        fixture = copy.deepcopy(self.fixture)
        invalid = copy.deepcopy(fixture['home_team_lastx']['doc'][0]['data']['matches'][0])
        invalid['_id'] = 99999999
        invalid['result'] = {'home': -1, 'away': 'invalid'}
        fixture['home_team_lastx']['doc'][0]['data']['matches'].append(invalid)
        evidence = build_football_evidence(
            self.candidate,
            match_info_payload=fixture['match_info'],
            home_team_lastx_payload=fixture['home_team_lastx'],
            away_team_lastx_payload=fixture['away_team_lastx'],
        )
        self.assertEqual(len(evidence.home_form.last_10), 3)

    def test_missing_evidence_returns_none_without_raising(self):
        wrong_candidate = make_candidate(event_id='sr:match:99999999')
        self.assertIsNone(self.provider.get_evidence(wrong_candidate, now=NOW))

    def test_incomplete_evidence_remains_identifiable(self):
        fixture = copy.deepcopy(self.fixture)
        fixture['away_team_lastx']['doc'][0]['data']['matches'] = []
        evidence = build_football_evidence(
            self.candidate,
            match_info_payload=fixture['match_info'],
            home_team_lastx_payload=fixture['home_team_lastx'],
            away_team_lastx_payload=fixture['away_team_lastx'],
        )
        self.assertFalse(evidence.data_quality.away_recent_form_available)
        self.assertFalse(evidence.data_quality.complete)
        self.assertIn('away_recent_form', evidence.data_quality.missing)

    def test_provider_probability_is_preserved_without_calibration(self):
        self.assertEqual(self.evidence.market.provider_probability, 0.691803)
        self.assertEqual(
            self.evidence.market.provider_probability_basis,
            'sportybet_decimal_probability',
        )

    def test_season_statistics_preserve_sample_size(self):
        self.assertIsNotNone(self.evidence.home_form.season_stats)
        self.assertEqual(self.evidence.home_form.season_stats.season_id, '140756')
        self.assertEqual(self.evidence.home_form.season_stats.goals_scored_average.value, 0.0)
        self.assertEqual(self.evidence.home_form.season_stats.goals_scored_average.sample_size, 3)
        self.assertEqual(self.evidence.home_form.season_stats.goals_conceded_average.value, 1.67)

    def test_evidence_is_fresh_only_within_max_age(self):
        self.assertTrue(is_evidence_fresh(self.evidence, now=NOW))
        self.assertFalse(
            is_evidence_fresh(self.evidence, now=NOW.replace(year=2027))
        )
        self.assertIsNone(
            self.provider.get_evidence(
                self.candidate,
                now=datetime.fromtimestamp(1789141401, timezone.utc),
            )
        )

    def test_normalized_team_name_fallback_matches_exact_names(self):
        candidate = make_candidate(
            event_id='provider-event-1',
            home_team_id=None,
            away_team_id=None,
            home_team_name='aston   villa',
            away_team_name='NOTTINGHAM',
        )
        context = find_matching_match_info(candidate, [self.fixture['match_info']])
        self.assertIsNotNone(context)
        self.assertEqual(context.match_id, '72221252')

    def test_ambiguous_name_fallback_is_rejected(self):
        candidate = make_candidate(
            event_id='provider-event-1',
            home_team_id=None,
            away_team_id=None,
            home_team_name='Aston Villa',
            away_team_name='Nottingham',
        )
        duplicate = copy.deepcopy(self.fixture['match_info'])
        self.assertIsNone(
            find_matching_match_info(
                candidate,
                [self.fixture['match_info'], duplicate],
            )
        )

    def test_collection_deduplicates_candidates_and_records_unavailable(self):
        available = make_candidate()
        unavailable = make_candidate(event_id='sr:match:99999999')
        collection = collect_football_evidence(
            [available, available, unavailable],
            self.provider,
            now=NOW,
        )
        self.assertEqual(list(collection.evidence_by_event_id), ['sr:match:72221252'])
        self.assertEqual(collection.unavailable_event_ids, ['sr:match:99999999'])


class PredictionEvidenceIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = load_fixture()
        self.season = load_season_fixture()
        self.candidate = make_candidate()
        self.provider = CapturedFootballEvidenceProvider(
            match_info_payload=self.fixture['match_info'],
            home_team_lastx_payload=self.fixture['home_team_lastx'],
            away_team_lastx_payload=self.fixture['away_team_lastx'],
            season_unique_team_stats_payload=self.season,
        )
        self.evidence = self.provider.get_evidence(self.candidate, now=NOW)

    def test_feature_snapshot_preserves_football_evidence(self):
        snapshot = extract_prediction_features(self.candidate, self.evidence)
        self.assertEqual(snapshot.football_evidence, self.evidence)
        self.assertEqual(snapshot.football_evidence.home_form.team_id, '40')

    def test_baseline_score_is_unchanged_by_evidence(self):
        without_evidence = evaluate_prediction(self.candidate)
        with_evidence = evaluate_prediction(
            self.candidate,
            football_evidence=self.evidence,
        )
        self.assertEqual(with_evidence.model_score, without_evidence.model_score)
        self.assertEqual(with_evidence.score_breakdown, without_evidence.score_breakdown)

    def test_evidence_feature_snapshot_persists(self):
        handle, path = tempfile.mkstemp(suffix='.sqlite3')
        os.close(handle)
        store = PredictionStore(path)
        try:
            evaluation = evaluate_prediction(
                self.candidate,
                football_evidence=self.evidence,
            )
            result = store.save_predictions([evaluation])
            self.assertEqual(result.inserted, 1)
            record = store.get_prediction(self.candidate.identity)
            self.assertEqual(record.feature_snapshot, evaluation.feature_snapshot)
            self.assertEqual(record.feature_snapshot.football_evidence, self.evidence)
        finally:
            os.unlink(path)


if __name__ == '__main__':
    unittest.main()
