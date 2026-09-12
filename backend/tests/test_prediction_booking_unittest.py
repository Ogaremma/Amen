import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from fastapi import HTTPException

from app.schemas.prediction import EvidenceQuality
from app.schemas.sportybet_markets import (
    OverOneHalfCandidate,
    SportyBetFixtureWithMarkets,
    SportyBetMarket,
    SportyBetMarketCatalogPage,
    SportyBetOutcome,
)
from app.services.prediction_booking import (
    PredictionBookingService,
    SPORTYBET_SELECTION_BATCH_SIZE,
    validate_prediction_booking_candidate,
)
from app.services.prediction_evidence_engine import evaluate_evidence_prediction
from app.services.prediction_store import PredictionStore
from app.services.sportybet_markets import extract_over_one_half_candidates
from backend.tests.test_prediction_evidence_engine_unittest import (
    complete_evidence,
    load_evidence,
)
from backend.tests.test_prediction_evidence_unittest import NOW as EVIDENCE_NOW


BOOKING_NOW = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
TODAY_KICKOFF = datetime(2026, 9, 12, 14, 0, tzinfo=timezone.utc)
TOMORROW_KICKOFF = datetime(2026, 9, 13, 14, 0, tzinfo=timezone.utc)


def fixture(
    event_id: str,
    kickoff: datetime,
    *,
    odds: float = 1.45,
    active: bool = True,
    probability: float | None = 0.691803,
) -> SportyBetFixtureWithMarkets:
    return SportyBetFixtureWithMarkets(
        event_id=event_id,
        game_id='40427',
        sport_id='sr:sport:1',
        sport_name='Football',
        category_id='sr:category:1',
        category_name='England',
        tournament_id='sr:tournament:17',
        tournament_name='Premier League',
        competition='Premier League',
        home_team_id='sr:competitor:40',
        home_team_name='Aston Villa',
        away_team_id='sr:competitor:14',
        away_team_name='Nottingham Forest',
        kickoff=kickoff,
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
                        odds=odds,
                        probability=probability,
                        is_active=active,
                    )
                ],
            )
        ],
    )


def candidate(*args, **kwargs) -> OverOneHalfCandidate:
    return extract_over_one_half_candidates([fixture(*args, **kwargs)])[0]


def catalogue(fixtures, *, retrieved_at=BOOKING_NOW, complete=True):
    return SportyBetMarketCatalogPage(
        total_num=len(fixtures),
        fixtures=fixtures,
        pages_fetched=1,
        retrieved_at=retrieved_at,
        complete=complete,
        retrieved_num=len(fixtures),
    )


def selected_evaluation(current_candidate: OverOneHalfCandidate):
    return evaluate_evidence_prediction(
        current_candidate,
        football_evidence=complete_evidence(load_evidence()),
        now=EVIDENCE_NOW,
    )


class PredictionBookingServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix='.sqlite3')
        os.close(handle)
        self.store = PredictionStore(self.path)
        self.service = PredictionBookingService(self.store)

    def tearDown(self):
        os.unlink(self.path)

    async def test_qualifying_and_prediction_pools_remain_separate(self):
        selected = candidate('sr:match:72221252', TODAY_KICKOFF)
        unselected = candidate('sr:match:2', TODAY_KICKOFF + timedelta(hours=1))
        tomorrow = candidate('sr:match:3', TOMORROW_KICKOFF)
        evaluations = [
            selected_evaluation(selected),
            evaluate_evidence_prediction(unselected, now=EVIDENCE_NOW),
        ]
        response = mock.AsyncMock(return_value=catalogue([
            selected.fixture,
            unselected.fixture,
            tomorrow.fixture,
        ]))
        create = mock.AsyncMock(side_effect=['QUAL-TODAY', 'QUAL-TOMORROW', 'PRED-TODAY'])

        with mock.patch(
            'app.services.prediction_booking.get_upcoming_football_market_fixtures',
            new=response,
        ), mock.patch(
            'app.services.prediction_booking._create_share_code',
            new=create,
        ):
            result = await self.service.create_booking_pools(evaluations, now=BOOKING_NOW)

        self.assertEqual(
            [selection.identity.event_id for selection in result.qualifying.today.selections],
            ['sr:match:72221252', 'sr:match:2'],
        )
        self.assertEqual(
            [selection.identity.event_id for selection in result.qualifying.tomorrow.selections],
            ['sr:match:3'],
        )
        self.assertEqual(
            [selection.identity.event_id for selection in result.predictions.today.selections],
            ['sr:match:72221252'],
        )
        self.assertEqual(result.predictions.tomorrow.selections, [])
        self.assertEqual(result.qualifying.today.booking_codes, ['QUAL-TODAY'])
        self.assertEqual(result.predictions.today.booking_codes, ['PRED-TODAY'])
        prediction_selection = result.predictions.today.selections[0]
        self.assertIsNotNone(prediction_selection.explanation)
        self.assertTrue(prediction_selection.explanation.positive_signals)
        prediction_identities = {
            selection.identity.model_dump_json()
            for day in (result.predictions.today, result.predictions.tomorrow)
            for selection in day.selections
        }
        qualifying_identities = {
            selection.identity.model_dump_json()
            for day in (result.qualifying.today, result.qualifying.tomorrow)
            for selection in day.selections
        }
        self.assertTrue(prediction_identities.issubset(qualifying_identities))

    async def test_booking_payload_preserves_exact_identity_and_no_team_names(self):
        selected = candidate('sr:match:72221252', TODAY_KICKOFF)
        evaluation = selected_evaluation(selected)
        create = mock.AsyncMock(return_value='PRED-TODAY')

        with mock.patch(
            'app.services.prediction_booking.get_upcoming_football_market_fixtures',
            new=mock.AsyncMock(return_value=catalogue([selected.fixture])),
        ), mock.patch(
            'app.services.prediction_booking._create_share_code',
            new=create,
        ):
            result = await self.service.create_booking_pools([evaluation], now=BOOKING_NOW)

        prediction_call = create.await_args_list[-1]
        self.assertEqual(
            prediction_call.args[0],
            [{
                'eventId': 'sr:match:72221252',
                'marketId': '18',
                'outcomeId': '12',
                'productId': 3,
                'sportId': 'sr:sport:1',
                'specifier': 'total=1.5',
            }],
        )
        self.assertNotIn('homeTeamName', prediction_call.args[0][0])
        self.assertNotIn('awayTeamName', prediction_call.args[0][0])
        self.assertEqual(result.predictions.today.booking_codes, ['PRED-TODAY'])

    async def test_current_market_disappearance_is_rejected(self):
        selected = candidate('sr:match:72221252', TODAY_KICKOFF)
        other = candidate('sr:match:2', TODAY_KICKOFF)
        evaluation = selected_evaluation(selected)
        create = mock.AsyncMock(return_value='QUAL-TODAY')

        with mock.patch(
            'app.services.prediction_booking.get_upcoming_football_market_fixtures',
            new=mock.AsyncMock(return_value=catalogue([other.fixture])),
        ), mock.patch(
            'app.services.prediction_booking._create_share_code',
            new=create,
        ):
            result = await self.service.create_booking_pools([evaluation], now=BOOKING_NOW)

        self.assertEqual(result.predictions.today.selections, [])
        self.assertEqual(
            result.predictions.today.rejected[0].reason,
            'current_event_unavailable',
        )
        self.assertEqual(result.predictions.today.booking_codes, [])

    async def test_changed_current_odds_outside_range_are_rejected(self):
        selected = candidate('sr:match:72221252', TODAY_KICKOFF)
        changed = fixture(
            'sr:match:72221252',
            TODAY_KICKOFF,
            odds=1.51,
        )
        evaluation = selected_evaluation(selected)
        create = mock.AsyncMock(return_value='QUAL-TODAY')

        with mock.patch(
            'app.services.prediction_booking.get_upcoming_football_market_fixtures',
            new=mock.AsyncMock(return_value=catalogue([changed])),
        ), mock.patch(
            'app.services.prediction_booking._create_share_code',
            new=create,
        ):
            result = await self.service.create_booking_pools([evaluation], now=BOOKING_NOW)

        rejection = result.predictions.today.rejected[0]
        self.assertEqual(rejection.reason, 'current_odds_outside_target_range')
        self.assertEqual(rejection.predicted_odds, 1.45)
        self.assertEqual(rejection.current_odds, 1.51)
        self.assertEqual(result.predictions.today.booking_codes, [])

    async def test_inactive_current_market_is_rejected(self):
        selected = candidate('sr:match:72221252', TODAY_KICKOFF)
        changed = fixture(
            'sr:match:72221252',
            TODAY_KICKOFF,
            active=False,
        )
        evaluation = selected_evaluation(selected)

        with mock.patch(
            'app.services.prediction_booking.get_upcoming_football_market_fixtures',
            new=mock.AsyncMock(return_value=catalogue([changed])),
        ), mock.patch(
            'app.services.prediction_booking._create_share_code',
            new=mock.AsyncMock(return_value='QUAL-TODAY'),
        ):
            result = await self.service.create_booking_pools([evaluation], now=BOOKING_NOW)

        self.assertEqual(
            result.predictions.today.rejected[0].reason,
            'current_outcome_inactive',
        )

    def test_validator_rejects_wrong_market_identity_fields(self):
        market_variants = {
            'wrong_market_id': ('19', '12', 3, 'total=1.5'),
            'wrong_specifier': ('18', '12', 3, 'total=2.5'),
            'wrong_outcome_id': ('18', '13', 3, 'total=1.5'),
            'wrong_product_id': ('18', '12', 1, 'total=1.5'),
        }
        for expected_reason, (market_id, outcome_id, product_id, specifier) in market_variants.items():
            with self.subTest(expected_reason=expected_reason):
                current = candidate('sr:match:72221252', TODAY_KICKOFF)
                current.market.market_id = market_id
                current.market.outcomes[0].outcome_id = outcome_id
                current.market.product_id = product_id
                current.market.specifier = specifier
                current.identity.market_id = market_id
                current.identity.outcome_id = outcome_id
                current.identity.product_id = product_id
                current.identity.specifier = specifier
                self.assertEqual(
                    validate_prediction_booking_candidate(current, now=BOOKING_NOW),
                    expected_reason,
                )

    def test_validator_rejects_inactive_and_out_of_range_odds(self):
        inactive = candidate('sr:match:72221252', TODAY_KICKOFF)
        high = candidate('sr:match:72221252', TODAY_KICKOFF)
        invalid = candidate('sr:match:72221252', TODAY_KICKOFF)
        inactive.outcome.is_active = False
        high.outcome.odds = 1.50
        invalid.outcome.odds = 0
        self.assertEqual(
            validate_prediction_booking_candidate(inactive, now=BOOKING_NOW),
            'outcome_is_inactive',
        )
        self.assertEqual(
            validate_prediction_booking_candidate(high, now=BOOKING_NOW),
            'odds_outside_target_range',
        )
        self.assertEqual(
            validate_prediction_booking_candidate(invalid, now=BOOKING_NOW),
            'invalid_odds',
        )

    def test_validator_accepts_today_and_tomorrow_and_rejects_other_windows(self):
        yesterday = candidate('sr:match:1', BOOKING_NOW - timedelta(hours=1))
        today = candidate('sr:match:2', TODAY_KICKOFF)
        tomorrow = candidate('sr:match:3', TOMORROW_KICKOFF)
        after_tomorrow = candidate(
            'sr:match:4',
            TOMORROW_KICKOFF + timedelta(days=1),
        )
        self.assertEqual(
            validate_prediction_booking_candidate(yesterday, now=BOOKING_NOW),
            'fixture_already_started',
        )
        self.assertIsNone(
            validate_prediction_booking_candidate(today, now=BOOKING_NOW)
        )
        self.assertIsNone(
            validate_prediction_booking_candidate(tomorrow, now=BOOKING_NOW)
        )
        self.assertEqual(
            validate_prediction_booking_candidate(after_tomorrow, now=BOOKING_NOW),
            'fixture_outside_today_tomorrow_window',
        )

    async def test_stale_catalogue_is_not_booked(self):
        selected = candidate('sr:match:72221252', TODAY_KICKOFF)
        evaluation = selected_evaluation(selected)
        stale = catalogue(
            [selected.fixture],
            retrieved_at=BOOKING_NOW - timedelta(seconds=301),
        )
        create = mock.AsyncMock()

        with mock.patch(
            'app.services.prediction_booking.get_upcoming_football_market_fixtures',
            new=mock.AsyncMock(return_value=stale),
        ), mock.patch(
            'app.services.prediction_booking._create_share_code',
            new=create,
        ):
            result = await self.service.create_booking_pools([evaluation], now=BOOKING_NOW)

        create.assert_not_awaited()
        self.assertFalse(result.catalogue.fresh)
        self.assertEqual(result.status.value, 'unavailable')
        self.assertEqual(
            result.predictions.today.rejected[0].reason,
            'current_catalogue_unavailable',
        )

    async def test_large_pool_is_batched_in_fifty_selections_without_drops(self):
        fixtures = [
            fixture(f'sr:match:{index}', TODAY_KICKOFF + timedelta(minutes=index))
            for index in range(SPORTYBET_SELECTION_BATCH_SIZE + 1)
        ]
        create = mock.AsyncMock(side_effect=['BATCH-1', 'BATCH-2'])

        with mock.patch(
            'app.services.prediction_booking.get_upcoming_football_market_fixtures',
            new=mock.AsyncMock(return_value=catalogue(fixtures)),
        ), mock.patch(
            'app.services.prediction_booking._create_share_code',
            new=create,
        ):
            result = await self.service.create_booking_pools([], now=BOOKING_NOW)

        self.assertEqual(
            [len(call.args[0]) for call in create.await_args_list],
            [50, 1],
        )
        self.assertEqual(result.qualifying.today.selection_count, 51)
        self.assertEqual(result.qualifying.today.booking_codes, ['BATCH-1', 'BATCH-2'])
        self.assertEqual(
            sum(len(batch.selections) for batch in result.qualifying.today.batches),
            51,
        )

    async def test_empty_pool_does_not_call_sportybet(self):
        create = mock.AsyncMock()
        with mock.patch(
            'app.services.prediction_booking.get_upcoming_football_market_fixtures',
            new=mock.AsyncMock(return_value=catalogue([])),
        ), mock.patch(
            'app.services.prediction_booking._create_share_code',
            new=create,
        ):
            result = await self.service.create_booking_pools([], now=BOOKING_NOW)

        create.assert_not_awaited()
        self.assertEqual(result.qualifying.today.status.value, 'empty')
        self.assertEqual(result.predictions.today.status.value, 'empty')
        self.assertEqual(result.status.value, 'complete')

    async def test_one_failed_batch_does_not_hide_other_valid_batch(self):
        today = candidate('sr:match:1', TODAY_KICKOFF)
        tomorrow = candidate('sr:match:2', TOMORROW_KICKOFF)
        create = mock.AsyncMock(
            side_effect=[HTTPException(status_code=502, detail='provider failed'), 'TOMORROW']
        )

        with mock.patch(
            'app.services.prediction_booking.get_upcoming_football_market_fixtures',
            new=mock.AsyncMock(return_value=catalogue([today.fixture, tomorrow.fixture])),
        ), mock.patch(
            'app.services.prediction_booking._create_share_code',
            new=create,
        ):
            result = await self.service.create_booking_pools([], now=BOOKING_NOW)

        self.assertEqual(result.qualifying.today.batches[0].status.value, 'failed')
        self.assertEqual(result.qualifying.today.batches[0].booking_code, None)
        self.assertEqual(result.qualifying.today.batches[0].error, 'provider failed')
        self.assertEqual(result.qualifying.tomorrow.booking_codes, ['TOMORROW'])
        self.assertEqual(result.status.value, 'partial')

    async def test_prediction_record_is_linked_and_exact_repeat_reuses_code(self):
        selected = candidate('sr:match:72221252', TODAY_KICKOFF)
        evaluation = selected_evaluation(selected)
        catalog = catalogue([selected.fixture])
        create = mock.AsyncMock(side_effect=['QUAL-1', 'PRED-1', 'QUAL-2'])

        with mock.patch(
            'app.services.prediction_booking.get_upcoming_football_market_fixtures',
            new=mock.AsyncMock(return_value=catalog),
        ), mock.patch(
            'app.services.prediction_booking._create_share_code',
            new=create,
        ):
            first = await self.service.create_booking_pools([evaluation], now=BOOKING_NOW)
            second = await self.service.create_booking_pools([evaluation], now=BOOKING_NOW)

        self.assertEqual(create.await_count, 3)
        self.assertEqual(first.predictions.today.batches[0].status.value, 'created')
        self.assertEqual(second.predictions.today.batches[0].status.value, 'reused')
        self.assertEqual(second.predictions.today.booking_codes, ['PRED-1'])
        record = self.store.get_prediction(selected.identity)
        self.assertEqual(record.booking_pool, 'predictions')
        self.assertEqual(record.booking_code, 'PRED-1')
        self.assertIsNotNone(record.booking_batch_identity)
        self.assertEqual(record.booking_created_at, BOOKING_NOW)

    async def test_selections_are_ordered_by_kickoff_then_event_id(self):
        fixtures = [
            fixture('sr:match:20', TODAY_KICKOFF),
            fixture('sr:match:10', TODAY_KICKOFF),
            fixture('sr:match:30', TODAY_KICKOFF + timedelta(minutes=1)),
        ]

        with mock.patch(
            'app.services.prediction_booking.get_upcoming_football_market_fixtures',
            new=mock.AsyncMock(return_value=catalogue(fixtures)),
        ), mock.patch(
            'app.services.prediction_booking._create_share_code',
            new=mock.AsyncMock(return_value='ORDERED'),
        ):
            result = await self.service.create_booking_pools([], now=BOOKING_NOW)

        self.assertEqual(
            [selection.identity.event_id for selection in result.qualifying.today.selections],
            ['sr:match:10', 'sr:match:20', 'sr:match:30'],
        )

    async def test_duplicate_identities_collapse_but_different_identities_remain(self):
        duplicate = fixture('sr:match:1', TODAY_KICKOFF)
        other = fixture('sr:match:2', TODAY_KICKOFF)
        create = mock.AsyncMock(return_value='DEDUPLICATED')

        with mock.patch(
            'app.services.prediction_booking.get_upcoming_football_market_fixtures',
            new=mock.AsyncMock(return_value=catalogue([
                duplicate,
                duplicate.model_copy(deep=True),
                other,
            ])),
        ), mock.patch(
            'app.services.prediction_booking._create_share_code',
            new=create,
        ):
            result = await self.service.create_booking_pools([], now=BOOKING_NOW)

        self.assertEqual(result.qualifying.today.selection_count, 2)
        self.assertEqual(len(create.await_args.args[0]), 2)
        self.assertEqual(
            [selection.identity.event_id for selection in result.qualifying.today.selections],
            ['sr:match:1', 'sr:match:2'],
        )

    async def test_changed_prediction_batch_identity_conflicts_rather_than_rebooking(self):
        first_selected = candidate('sr:match:1', TODAY_KICKOFF)
        second_selected = candidate('sr:match:2', TODAY_KICKOFF)
        create = mock.AsyncMock(side_effect=['QUAL-1', 'PRED-1', 'QUAL-2'])

        with mock.patch(
            'app.services.prediction_booking.get_upcoming_football_market_fixtures',
            new=mock.AsyncMock(return_value=catalogue([first_selected.fixture])),
        ), mock.patch(
            'app.services.prediction_booking._create_share_code',
            new=create,
        ):
            await self.service.create_booking_pools(
                [selected_evaluation(first_selected)],
                now=BOOKING_NOW,
            )

        with mock.patch(
            'app.services.prediction_booking.get_upcoming_football_market_fixtures',
            new=mock.AsyncMock(return_value=catalogue([
                first_selected.fixture,
                second_selected.fixture,
            ])),
        ), mock.patch(
            'app.services.prediction_booking._create_share_code',
            new=create,
        ):
            result = await self.service.create_booking_pools(
                [selected_evaluation(first_selected), selected_evaluation(second_selected)],
                now=BOOKING_NOW,
            )

        self.assertEqual(create.await_count, 3)
        self.assertEqual(
            result.predictions.today.batches[0].status.value,
            'failed',
        )
        self.assertEqual(
            result.predictions.today.batches[0].error,
            'prediction_booking_idempotency_conflict',
        )
        self.assertEqual(result.predictions.today.booking_codes, [])
