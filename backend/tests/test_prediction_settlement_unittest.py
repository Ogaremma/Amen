from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from app.schemas.booking import BookingResponse, BookingSelection
from app.schemas.prediction import PredictionStatus
from app.schemas.prediction_settlement import (
    PredictionMatchStatus,
    PredictionSettlementUpdate,
)
from app.services.prediction_engine import evaluate_prediction
from app.services.prediction_settlement import (
    PredictionSettlementService,
    normalize_match_status,
)
from app.services.prediction_store import PredictionStore
from app.services.sportybet import _parse_set_score, parse_booking
from backend.tests.test_bookings_unittest import make_payload
from backend.tests.test_prediction_core_unittest import make_candidate


UTC = timezone.utc
NOW = datetime(2026, 9, 12, 18, 0, tzinfo=UTC)
KICKOFF = NOW - timedelta(hours=2)


def save_prediction(
    store: PredictionStore,
    *,
    event_id: str = 'sr:match:settlement',
    kickoff: datetime = KICKOFF,
    booking_code: str | None = 'PRED1',
) -> object:
    candidate = make_candidate(event_id=event_id, kickoff=kickoff)
    evaluation = evaluate_prediction(candidate)
    assert evaluation.selected
    store.save_predictions([evaluation])
    if booking_code is not None:
        store.attach_booking_metadata(
            [evaluation.identity],
            booking_pool='predictions',
            booking_code=booking_code,
            booking_batch_identity='batch-1',
            booking_created_at=NOW - timedelta(hours=3),
        )
    record = store.get_prediction(evaluation.identity)
    assert record is not None
    return record


def ticket_selection(record: object, **overrides) -> BookingSelection:
    values = {
        'id': record.event_id,
        'event_id': record.event_id,
        'market_id': record.market_id,
        'outcome_id': record.outcome_id,
        'product_id': record.product_id,
        'sport_id': record.sport_id,
        'home': 'Preserved Home Name',
        'away': 'Preserved Away Name',
        'competition': 'Preserved Competition',
        'category': 'Preserved Category',
        'kickoff': record.kickoff_at,
        'kickoff_date': record.kickoff_at.strftime('%Y-%m-%d'),
        'kickoff_time': record.kickoff_at.strftime('%H:%M'),
        'local_kickoff_date': record.kickoff_at.strftime('%Y-%m-%d'),
        'local_kickoff_time': record.kickoff_at.strftime('%H:%M'),
        'market': 'Over/Under',
        'outcome': 'Over 1.5',
        'odds': 1.43,
        'specifier': record.specifier,
        'status': 'Ended',
        'home_score': 1,
        'away_score': 1,
        'game_status': 'ended',
        'result_status': 'pending',
    }
    values.update(overrides)
    return BookingSelection(**values)


def booking_response(selections: list[BookingSelection], code: str = 'PRED1') -> BookingResponse:
    return BookingResponse(
        booking_code=code,
        total_selections=len(selections),
        total_odds=1.43,
        remaining_odds=0.0,
        selections=selections,
    )


class TicketScoreParserTests(unittest.TestCase):
    def test_ticket_identity_and_final_score_are_preserved(self):
        payload = make_payload()
        event = payload['data']['outcomes'][0]
        event['matchStatus'] = 'Ended'
        event['setScore'] = '2:3'

        result = parse_booking('HW7UDH', payload, now=NOW)

        selection = next(
            item for item in result.selections if item.event_id == 'sr:match:1001'
        )
        self.assertEqual(selection.product_id, 3)
        self.assertEqual(selection.sport_id, 'sr:sport:1')
        self.assertEqual(selection.home_score, 2)
        self.assertEqual(selection.away_score, 3)

    def test_invalid_scores_are_not_invented(self):
        for value in (None, '', '2', '2:3:4', '-1:2', '2:-3', 'two:1', '1:two'):
            with self.subTest(value=value):
                self.assertEqual(_parse_set_score(value), (None, None))

    def test_status_normalization_is_conservative(self):
        cases = {
            'Ended': PredictionMatchStatus.final,
            'Finished': PredictionMatchStatus.final,
            'Live': PredictionMatchStatus.live,
            'In Progress': PredictionMatchStatus.live,
            'Not start': PredictionMatchStatus.upcoming,
            'Postponed': PredictionMatchStatus.postponed,
            'Cancelled': PredictionMatchStatus.cancelled,
            'Canceled': PredictionMatchStatus.cancelled,
            'Abandoned': PredictionMatchStatus.abandoned,
            'Mystery': PredictionMatchStatus.unknown,
        }
        for raw_status, expected in cases.items():
            with self.subTest(raw_status=raw_status):
                self.assertEqual(normalize_match_status(raw_status), expected)


class PredictionSettlementServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix='.sqlite3')
        os.close(handle)
        self.store = PredictionStore(self.path)

    def tearDown(self):
        os.unlink(self.path)

    async def test_completed_scores_settle_from_actual_goals(self):
        cases = [
            ((0, 0), PredictionStatus.settled_miss, 0, 'miss'),
            ((1, 0), PredictionStatus.settled_miss, 1, 'miss'),
            ((0, 1), PredictionStatus.settled_miss, 1, 'miss'),
            ((1, 1), PredictionStatus.settled_win, 2, 'win'),
            ((2, 0), PredictionStatus.settled_win, 2, 'win'),
            ((0, 2), PredictionStatus.settled_win, 2, 'win'),
            ((2, 1), PredictionStatus.settled_win, 3, 'win'),
        ]
        records = []
        selections = []
        for score, _, _, _ in cases:
            record = save_prediction(
                self.store,
                event_id=f'sr:match:{score[0]}-{score[1]}',
                booking_code=f'CODE{score[0]}{score[1]}',
            )
            records.append(record)
            selections.append(
                ticket_selection(record, home_score=score[0], away_score=score[1])
            )
        provider = mock.AsyncMock(return_value=booking_response(selections))
        service = PredictionSettlementService(self.store, booking_provider=provider)

        summary = await service.monitor_and_settle_predictions(now=NOW)

        self.assertEqual(summary.settled_miss, 3)
        self.assertEqual(summary.settled_win, 4)
        for (score, status, goals, result), record in zip(cases, records):
            with self.subTest(score=score):
                settled = self.store.get_prediction(_identity(record))
                self.assertEqual(settled.prediction_status, status)
                self.assertEqual(settled.actual_goals, goals)
                self.assertEqual(settled.actual_result, result)
                self.assertIsNotNone(settled.settled_at)

    async def test_live_match_is_not_settled(self):
        record = save_prediction(self.store)
        provider = mock.AsyncMock(
            return_value=booking_response(
                [ticket_selection(record, status='Live', game_status='live', home_score=1, away_score=1)]
            )
        )
        service = PredictionSettlementService(self.store, booking_provider=provider)

        summary = await service.monitor_and_settle_predictions(now=NOW)

        result = self.store.get_prediction(_identity(record))
        self.assertEqual(result.prediction_status, PredictionStatus.live)
        self.assertEqual((result.current_home_goals, result.current_away_goals), (1, 1))
        self.assertIsNone(result.actual_goals)
        self.assertIsNone(result.actual_result)
        self.assertIsNone(result.settled_at)
        self.assertEqual(summary.live, 1)

    async def test_non_result_statuses_do_not_become_misses(self):
        cases = {
            'Postponed': PredictionStatus.postponed,
            'Cancelled': PredictionStatus.cancelled,
            'Abandoned': PredictionStatus.abandoned,
            'Unknown': PredictionStatus.unresolved,
        }
        records = []
        selections = []
        for raw_status in cases:
            record = save_prediction(
                self.store,
                event_id=f'sr:match:{raw_status.lower()}',
                booking_code=f'CODE{raw_status}',
            )
            records.append(record)
            selections.append(ticket_selection(record, status=raw_status))
        provider = mock.AsyncMock(return_value=booking_response(selections))
        service = PredictionSettlementService(self.store, booking_provider=provider)

        await service.monitor_and_settle_predictions(now=NOW)

        for (raw_status, expected), record in zip(cases.items(), records):
            with self.subTest(raw_status=raw_status):
                result = self.store.get_prediction(_identity(record))
                self.assertEqual(result.prediction_status, expected)
                self.assertIsNone(result.actual_goals)
                self.assertIsNone(result.actual_result)
                self.assertIsNone(result.settled_at)

    async def test_invalid_final_score_is_unresolved(self):
        for home_score, away_score in ((None, 1), (1, None), (None, None)):
            with self.subTest(score=(home_score, away_score)):
                record = save_prediction(self.store, booking_code=f'INVALID{home_score}{away_score}')
                provider = mock.AsyncMock(
                    return_value=booking_response(
                        [ticket_selection(record, home_score=home_score, away_score=away_score)]
                    )
                )
                service = PredictionSettlementService(self.store, booking_provider=provider)

                summary = await service.monitor_and_settle_predictions(now=NOW)

                result = self.store.get_prediction(_identity(record))
                self.assertEqual(result.prediction_status, PredictionStatus.unresolved)
                self.assertIsNone(result.actual_goals)
                self.assertIsNone(result.settled_at)
                self.assertEqual(summary.unresolved, 1)

    async def test_exact_six_field_identity_is_required(self):
        cases = [
            {'market_id': '19'},
            {'product_id': None},
            {'event_id': 'sr:match:other', 'id': 'sr:match:other'},
        ]
        for index, overrides in enumerate(cases):
            with self.subTest(case=index):
                stored = save_prediction(
                    self.store,
                    event_id=f'sr:match:identity-{index}',
                    booking_code=f'IDENT{index}',
                )
                selection = ticket_selection(stored, **overrides)
                provider = mock.AsyncMock(return_value=booking_response([selection]))
                service = PredictionSettlementService(self.store, booking_provider=provider)

                summary = await service.monitor_and_settle_predictions(now=NOW)

                result = self.store.get_prediction(_identity(stored))
                self.assertEqual(result.prediction_status, PredictionStatus.unresolved)
                self.assertIn(summary.diagnostics[0].reason, {
                    'identity_mismatch',
                    'exact_identity_not_found',
                })

    async def test_ambiguous_exact_identity_is_rejected(self):
        record = save_prediction(self.store)
        duplicate = ticket_selection(record)
        provider = mock.AsyncMock(
            return_value=booking_response([ticket_selection(record), duplicate])
        )
        service = PredictionSettlementService(self.store, booking_provider=provider)

        summary = await service.monitor_and_settle_predictions(now=NOW)

        result = self.store.get_prediction(_identity(record))
        self.assertEqual(result.prediction_status, PredictionStatus.unresolved)
        self.assertEqual(summary.diagnostics[0].reason, 'ambiguous_selection_identity')

    async def test_provider_failure_is_reported_and_does_not_block_other_codes(self):
        failed = save_prediction(self.store, event_id='sr:match:failed', booking_code='FAIL')
        successful = save_prediction(
            self.store,
            event_id='sr:match:successful',
            booking_code='SUCCESS',
        )

        async def provider(code: str) -> BookingResponse:
            if code == 'FAIL':
                raise RuntimeError('temporary provider failure')
            return booking_response([ticket_selection(successful)], code=code)

        service = PredictionSettlementService(self.store, booking_provider=provider)
        summary = await service.monitor_and_settle_predictions(now=NOW)

        failed_result = self.store.get_prediction(_identity(failed))
        successful_result = self.store.get_prediction(_identity(successful))
        self.assertEqual(failed_result.prediction_status, PredictionStatus.pending)
        self.assertEqual(successful_result.prediction_status, PredictionStatus.settled_win)
        self.assertEqual(summary.provider_failures, 1)
        self.assertEqual(summary.provider_calls, 2)
        self.assertEqual(summary.settled_win, 1)

    async def test_malformed_booking_response_is_a_provider_failure(self):
        record = save_prediction(self.store)
        provider = mock.AsyncMock(return_value={'unexpected': 'payload'})
        service = PredictionSettlementService(self.store, booking_provider=provider)

        summary = await service.monitor_and_settle_predictions(now=NOW)

        result = self.store.get_prediction(_identity(record))
        self.assertEqual(result.prediction_status, PredictionStatus.pending)
        self.assertEqual(summary.provider_failures, 1)
        self.assertEqual(summary.diagnostics[0].reason, 'invalid_booking_response')

    async def test_missing_booking_code_is_unresolved(self):
        record = save_prediction(self.store, booking_code=None)
        provider = mock.AsyncMock()
        service = PredictionSettlementService(self.store, booking_provider=provider)

        summary = await service.monitor_and_settle_predictions(now=NOW)

        result = self.store.get_prediction(_identity(record))
        self.assertEqual(result.prediction_status, PredictionStatus.unresolved)
        self.assertEqual(summary.unresolved, 1)
        provider.assert_not_called()

    async def test_future_prediction_is_not_polled_early(self):
        record = save_prediction(
            self.store,
            kickoff=NOW + timedelta(hours=2),
            booking_code='FUTURE',
        )
        provider = mock.AsyncMock()
        service = PredictionSettlementService(self.store, booking_provider=provider)

        summary = await service.monitor_and_settle_predictions(now=NOW)

        self.assertEqual(summary.not_due, 1)
        self.assertEqual(summary.provider_calls, 0)
        self.assertEqual(self.store.get_prediction(_identity(record)).prediction_status, PredictionStatus.pending)

    async def test_settlement_is_idempotent_and_preserves_prediction_time_fields(self):
        win_record = save_prediction(
            self.store,
            event_id='sr:match:idempotent-win',
            booking_code='IDEMPOTENT-WIN',
        )
        miss_record = save_prediction(
            self.store,
            event_id='sr:match:idempotent-miss',
            booking_code='IDEMPOTENT-MISS',
        )
        provider = mock.AsyncMock(
            return_value=booking_response([
                ticket_selection(win_record, home_score=2, away_score=1),
                ticket_selection(miss_record, home_score=1, away_score=0),
            ])
        )
        service = PredictionSettlementService(self.store, booking_provider=provider)
        first_summary = await service.monitor_and_settle_predictions(now=NOW)
        settled_win = self.store.get_prediction(_identity(win_record))
        settled_miss = self.store.get_prediction(_identity(miss_record))

        second_summary = await service.monitor_and_settle_predictions(now=NOW)

        unchanged_win = self.store.get_prediction(_identity(win_record))
        unchanged_miss = self.store.get_prediction(_identity(miss_record))
        self.assertEqual(first_summary.settled_win, 1)
        self.assertEqual(first_summary.settled_miss, 1)
        self.assertEqual(second_summary.checked, 0)
        self.assertEqual(second_summary.provider_calls, 0)
        for record, unchanged in ((win_record, unchanged_win), (miss_record, unchanged_miss)):
            self.assertEqual(unchanged.odds_at_prediction, record.odds_at_prediction)
            self.assertEqual(unchanged.provider_probability, record.provider_probability)
            self.assertEqual(unchanged.evidence_score, record.evidence_score)
            self.assertEqual(unchanged.feature_snapshot, record.feature_snapshot)
            self.assertEqual(unchanged.model_version, record.model_version)
            self.assertEqual(unchanged.booking_code, record.booking_code)
            self.assertEqual(unchanged.booking_batch_identity, record.booking_batch_identity)
        self.assertEqual(unchanged_win.actual_result, 'win')
        self.assertEqual(unchanged_miss.actual_result, 'miss')

    def test_conflicting_later_result_is_surfaced_not_overwritten(self):
        record = save_prediction(self.store, event_id='sr:match:conflict')
        settled = self.store.apply_settlement_update(
            PredictionSettlementUpdate(
                prediction_id=record.id,
                event_id=record.event_id,
                prediction_status=PredictionStatus.settled_win,
                live_status='Ended',
                current_home_goals=2,
                current_away_goals=0,
                actual_goals=2,
                actual_result='win',
                settled_at=NOW,
            )
        )
        self.assertEqual(settled[0].value, 'updated')

        outcome, current = self.store.apply_settlement_update(
            PredictionSettlementUpdate(
                prediction_id=record.id,
                event_id=record.event_id,
                prediction_status=PredictionStatus.settled_miss,
                live_status='Ended',
                current_home_goals=1,
                current_away_goals=0,
                actual_goals=1,
                actual_result='miss',
                settled_at=NOW,
            )
        )

        self.assertEqual(outcome.value, 'conflict')
        self.assertEqual(current.prediction_status, PredictionStatus.settled_win)
        self.assertEqual(current.actual_goals, 2)
        self.assertEqual(current.actual_result, 'win')


def _identity(record: object):
    from app.schemas.sportybet_markets import SportyBetSelectionIdentity

    return SportyBetSelectionIdentity(
        event_id=record.event_id,
        market_id=record.market_id,
        outcome_id=record.outcome_id,
        product_id=record.product_id,
        sport_id=record.sport_id,
        specifier=record.specifier,
    )


if __name__ == '__main__':
    unittest.main()
