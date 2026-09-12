from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest import mock

from app.schemas.prediction_booking import (
    PredictionBookingBatchStatus,
    PredictionBookingDay,
    PredictionBookingDayStatus,
    PredictionBookingGroup,
    PredictionBookingPool,
)
from app.schemas.prediction import PredictionStatus
from app.schemas.prediction_settlement import PredictionSettlementUpdate
from app.schemas.prediction_daily_production import (
    DailyProductionDeliveryOutcome,
    PredictionMessageCategory,
    PredictionMessageDeliveryStatus,
)
from app.services.prediction_booking import PredictionBookingService
from app.services.prediction_engine import evaluate_prediction
from app.services.prediction_daily_production import (
    DailyPredictionProductionService,
    prediction_message_delivery_identity,
)
from app.services.prediction_grouping import split_prediction_selections
from app.services.prediction_production_time import (
    PRODUCTION_PREDICTION_TIMEZONE,
    PRODUCTION_PREDICTION_TIMEZONE_NAME,
    production_daily_prediction_window,
    production_prediction_window,
)
from app.services.prediction_store import PredictionStore
from app.telegram.prediction_formatter import (
    TELEGRAM_MESSAGE_SAFE_UTF16_LIMIT,
    TelegramPredictionMessageTooLarge,
)
from backend.tests.test_prediction_booking_unittest import (
    catalogue,
    candidate,
    selected_evaluation,
)
from backend.tests.test_prediction_core_unittest import make_candidate
from backend.tests.test_telegram_prediction_delivery_unittest import (
    NOW as TELEGRAM_NOW,
    TODAY,
    TOMORROW,
    batch,
    day_result,
    explanation,
    result_with,
    selection,
)


UTC = timezone.utc
NIGERIA_MIDNIGHT_UTC = datetime(2026, 9, 12, 23, 0, tzinfo=UTC)
BEFORE_NIGERIA_MIDNIGHT_UTC = NIGERIA_MIDNIGHT_UTC - timedelta(seconds=1)
AFTER_NIGERIA_MIDNIGHT_UTC = NIGERIA_MIDNIGHT_UTC + timedelta(seconds=1)


class FakeEvaluationProvider:
    async def evaluate(self, *, now: datetime) -> list[Any]:
        return []


class FakeBookingService:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls = 0

    async def create_booking_pools(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        return self.result


class RecordingTelegramTransport:
    def __init__(self, *fail_predicates: Any) -> None:
        self.messages: list[dict[str, Any]] = []
        self.fail_predicates = list(fail_predicates)

    async def send_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        if any(predicate(payload) for predicate in self.fail_predicates):
            raise RuntimeError('telegram unavailable')
        self.messages.append(payload)
        return {'ok': True}


def prediction_group(
    day: PredictionBookingDay,
    group_index: int,
    selections: list[Any],
    code: str,
) -> PredictionBookingGroup:
    group_batch = batch(
        PredictionBookingPool.predictions,
        day,
        selections,
        index=group_index,
        code=code,
    )
    return PredictionBookingGroup(
        group_index=group_index,
        label=f'Group {group_index}',
        selection_count=len(selections),
        selections=selections,
        batches=[group_batch],
        booking_codes=[code],
        status=PredictionBookingDayStatus.complete,
    )


def grouped_prediction_day(
    day: PredictionBookingDay,
    selections: list[Any],
) -> Any:
    groups = split_prediction_selections(selections)
    group_models = [
        prediction_group(
            day,
            index,
            group_selections,
            f'PRED-{day.value.upper()}-{index}',
        )
        for index, group_selections in enumerate(groups, start=1)
    ]
    batches = [group_batch for group in group_models for group_batch in group.batches]
    result = day_result(
        PredictionBookingPool.predictions,
        day,
        selections=selections,
        batches=batches,
    )
    result.groups = group_models
    return result


def production_result(
    *,
    today_predictions: int = 4,
    tomorrow_predictions: int = 4,
) -> Any:
    qualifying_today = day_result(
        PredictionBookingPool.qualifying,
        PredictionBookingDay.today,
        selections=[
            selection('sr:match:qualifying-today-1', TODAY),
            selection('sr:match:qualifying-today-2', TODAY + timedelta(minutes=30)),
        ],
        batches=[
            batch(
                PredictionBookingPool.qualifying,
                PredictionBookingDay.today,
                [
                    selection('sr:match:qualifying-today-1', TODAY),
                    selection('sr:match:qualifying-today-2', TODAY + timedelta(minutes=30)),
                ],
                code='QUAL-TODAY',
            )
        ],
    )
    qualifying_tomorrow = day_result(
        PredictionBookingPool.qualifying,
        PredictionBookingDay.tomorrow,
        selections=[selection('sr:match:qualifying-tomorrow', TOMORROW)],
        batches=[
            batch(
                PredictionBookingPool.qualifying,
                PredictionBookingDay.tomorrow,
                [selection('sr:match:qualifying-tomorrow', TOMORROW)],
                code='QUAL-TOMORROW',
            )
        ],
    )
    today_selections = [
        selection(
            f'sr:match:prediction-today-{index}',
            TODAY + timedelta(minutes=index * 15),
            evidence_score=0.72 + index / 100,
            explanation=explanation(),
        )
        for index in range(1, today_predictions + 1)
    ]
    tomorrow_selections = [
        selection(
            f'sr:match:prediction-tomorrow-{index}',
            TOMORROW + timedelta(minutes=index * 15),
            evidence_score=0.73 + index / 100,
            explanation=explanation(),
        )
        for index in range(1, tomorrow_predictions + 1)
    ]
    return result_with(
        qualifying_today=qualifying_today,
        qualifying_tomorrow=qualifying_tomorrow,
        prediction_today=grouped_prediction_day(
            PredictionBookingDay.today,
            today_selections,
        ),
        prediction_tomorrow=grouped_prediction_day(
            PredictionBookingDay.tomorrow,
            tomorrow_selections,
        ),
    )


class ProductionTimezoneTests(unittest.TestCase):
    def test_midnight_boundary_uses_africa_lagos(self):
        before = production_daily_prediction_window(BEFORE_NIGERIA_MIDNIGHT_UTC)
        midnight = production_daily_prediction_window(NIGERIA_MIDNIGHT_UTC)
        after = production_daily_prediction_window(AFTER_NIGERIA_MIDNIGHT_UTC)

        self.assertEqual((str(before.today), str(before.tomorrow)), ('2026-09-12', '2026-09-13'))
        self.assertEqual((str(midnight.today), str(midnight.tomorrow)), ('2026-09-13', '2026-09-14'))
        self.assertEqual((str(after.today), str(after.tomorrow)), ('2026-09-13', '2026-09-14'))
        self.assertEqual(midnight.timezone_name, PRODUCTION_PREDICTION_TIMEZONE_NAME)

    def test_window_starts_at_lagos_midnight_in_utc(self):
        window = production_prediction_window(NIGERIA_MIDNIGHT_UTC)
        self.assertEqual(window.start, NIGERIA_MIDNIGHT_UTC)
        self.assertEqual(
            window.end_exclusive,
            NIGERIA_MIDNIGHT_UTC + timedelta(days=2),
        )

    def test_named_timezone_does_not_use_server_local_time(self):
        summer = datetime(2026, 7, 1, tzinfo=UTC)
        winter = datetime(2026, 1, 1, tzinfo=UTC)
        self.assertEqual(PRODUCTION_PREDICTION_TIMEZONE.utcoffset(summer), timedelta(hours=1))
        self.assertEqual(PRODUCTION_PREDICTION_TIMEZONE.utcoffset(winter), timedelta(hours=1))


class PredictionGroupingTests(unittest.TestCase):
    def test_group_counts_are_deterministic_and_disjoint(self):
        for count, expected in ((0, []), (1, [1]), (2, [1, 1]), (3, [2, 1]), (4, [2, 2]), (5, [3, 2])):
            with self.subTest(count=count):
                selections = [
                    selection(
                        f'sr:match:{index}',
                        TODAY + timedelta(minutes=index * 10),
                    )
                    for index in range(count)
                ]
                first = split_prediction_selections(selections)
                second = split_prediction_selections(list(reversed(selections)))
                self.assertEqual(
                    [len(group) for group in first],
                    expected,
                )
                self.assertEqual(first, second)
                all_identities = [
                    item.identity.event_id
                    for group in first
                    for item in group
                ]
                self.assertEqual(len(all_identities), len(set(all_identities)))

    def test_groups_are_chronologically_ordered(self):
        selections = [
            selection('sr:match:3', TODAY + timedelta(minutes=30)),
            selection('sr:match:1', TODAY),
            selection('sr:match:4', TODAY + timedelta(minutes=45)),
            selection('sr:match:2', TODAY + timedelta(minutes=15)),
        ]
        groups = split_prediction_selections(selections)
        self.assertEqual(
            [item.identity.event_id for item in groups[0]],
            ['sr:match:1', 'sr:match:3'],
        )
        self.assertEqual(
            [item.identity.event_id for item in groups[1]],
            ['sr:match:2', 'sr:match:4'],
        )


class GroupedBookingServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix='.sqlite3')
        os.close(handle)
        self.store = PredictionStore(self.path)

    def tearDown(self):
        os.unlink(self.path)

    async def test_grouped_booking_creates_separate_codes_per_group(self):
        now = NIGERIA_MIDNIGHT_UTC
        today_candidates = [
            candidate(
                f'sr:match:today-{index}',
                now + timedelta(hours=index),
            )
            for index in range(1, 5)
        ]
        tomorrow_candidates = [
            candidate(
                f'sr:match:tomorrow-{index}',
                now + timedelta(days=1, hours=index),
            )
            for index in range(1, 5)
        ]
        fixtures = [item.fixture for item in today_candidates + tomorrow_candidates]
        evaluations = [
            selected_evaluation(item)
            for item in today_candidates + tomorrow_candidates
        ]
        codes = iter(f'CODE-{index}' for index in range(1, 20))

        with mock.patch(
            'app.services.prediction_booking.get_upcoming_football_market_fixtures',
            new=mock.AsyncMock(return_value=catalogue(fixtures, retrieved_at=now)),
        ), mock.patch(
            'app.services.prediction_booking._create_share_code',
            new=mock.AsyncMock(side_effect=lambda selections: next(codes)),
        ):
            result = await PredictionBookingService(self.store).create_booking_pools(
                evaluations,
                now=now,
                window=production_prediction_window(now),
                day_timezone=PRODUCTION_PREDICTION_TIMEZONE,
                prediction_groups=True,
            )

        for day in (result.predictions.today, result.predictions.tomorrow):
            self.assertEqual(len(day.groups), 2)
            self.assertEqual(
                [len(group.selections) for group in day.groups],
                [2, 2],
            )
            self.assertEqual(len(day.booking_codes), 2)
            first = {item.identity.event_id for item in day.groups[0].selections}
            second = {item.identity.event_id for item in day.groups[1].selections}
            self.assertFalse(first & second)
            self.assertEqual(len(first | second), 4)


class DailyPredictionProductionServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix='.sqlite3')
        os.close(handle)
        self.store = PredictionStore(self.path)

    def tearDown(self):
        os.unlink(self.path)

    def service(
        self,
        result: Any,
        transport: RecordingTelegramTransport,
        *,
        now: datetime = NIGERIA_MIDNIGHT_UTC,
    ) -> tuple[DailyPredictionProductionService, FakeBookingService]:
        booking = FakeBookingService(result)
        service = DailyPredictionProductionService(
            evaluation_provider=FakeEvaluationProvider(),
            booking_service=booking,
            telegram_transport=transport,
            store=self.store,
            clock=lambda: now,
        )
        return service, booking

    async def test_successful_run_sends_exactly_two_separate_messages(self):
        transport = RecordingTelegramTransport()
        service, booking = self.service(production_result(), transport)

        result = await service.run_daily_prediction_delivery(555)

        self.assertEqual(result.outcome, DailyProductionDeliveryOutcome.delivered)
        self.assertEqual(booking.calls, 1)
        self.assertEqual(len(transport.messages), 2)
        self.assertIn('OVER 1.40–1.50 GAMES', transport.messages[0]['text'])
        self.assertNotIn('OVER 1.40–1.50 PREDICTIONS', transport.messages[0]['text'])
        self.assertIn('OVER 1.40–1.50 PREDICTIONS', transport.messages[1]['text'])
        self.assertNotIn('OVER 1.40–1.50 GAMES —', transport.messages[1]['text'])
        self.assertEqual(result.qualifying_delivery.status, PredictionMessageDeliveryStatus.delivered)
        self.assertEqual(result.prediction_delivery.status, PredictionMessageDeliveryStatus.delivered)

    async def test_prediction_message_contains_four_groups_and_exact_copy_payloads(self):
        transport = RecordingTelegramTransport()
        service, _ = self.service(production_result(), transport)

        await service.run_daily_prediction_delivery(555)

        prediction_message = transport.messages[1]
        text = prediction_message['text']
        self.assertEqual(text.count('TODAY — PREDICTION GROUP 1'), 1)
        self.assertEqual(text.count('TODAY — PREDICTION GROUP 2'), 1)
        self.assertEqual(text.count('TOMORROW — PREDICTION GROUP 1'), 1)
        self.assertEqual(text.count('TOMORROW — PREDICTION GROUP 2'), 1)
        self.assertIn('Why selected:', text)
        self.assertIn('Strong recent Over 1.5 evidence', text)
        buttons = [
            button
            for row in prediction_message['reply_markup']['inline_keyboard']
            for button in row
        ]
        self.assertEqual(len(buttons), 4)
        self.assertEqual(
            {button['copy_text']['text'] for button in buttons},
            {'PRED-TODAY-1', 'PRED-TODAY-2', 'PRED-TOMORROW-1', 'PRED-TOMORROW-2'},
        )

    async def test_fewer_predictions_produce_fewer_groups_and_codes(self):
        transport = RecordingTelegramTransport()
        result_data = production_result(today_predictions=1, tomorrow_predictions=0)
        service, _ = self.service(result_data, transport)

        result = await service.run_daily_prediction_delivery(555)

        self.assertEqual(result.outcome, DailyProductionDeliveryOutcome.delivered)
        prediction_text = transport.messages[1]['text']
        self.assertEqual(prediction_text.count('TODAY — PREDICTION GROUP 1'), 1)
        self.assertNotIn('TODAY — PREDICTION GROUP 2', prediction_text)
        self.assertIn('No model-selected predictions meet the current evidence threshold.', prediction_text)
        buttons = [
            button
            for row in transport.messages[1].get('reply_markup', {}).get('inline_keyboard', [])
            for button in row
        ]
        self.assertEqual(len(buttons), 1)
        self.assertEqual(buttons[0]['copy_text']['text'], 'PRED-TODAY-1')

    async def test_repeated_success_does_not_send_or_rebook_again(self):
        transport = RecordingTelegramTransport()
        service, booking = self.service(production_result(), transport)

        first = await service.run_daily_prediction_delivery(555)
        second = await service.run_daily_prediction_delivery(555)

        self.assertEqual(first.outcome, DailyProductionDeliveryOutcome.delivered)
        self.assertEqual(second.outcome, DailyProductionDeliveryOutcome.already_delivered)
        self.assertEqual(len(transport.messages), 2)
        self.assertEqual(booking.calls, 1)

    async def test_prediction_failure_retries_only_prediction_message(self):
        failing = RecordingTelegramTransport(
            lambda payload: 'OVER 1.40–1.50 PREDICTIONS' in payload['text']
        )
        service, booking = self.service(production_result(), failing)
        first = await service.run_daily_prediction_delivery(555)

        successful = RecordingTelegramTransport()
        service, retry_booking = self.service(production_result(), successful)
        second = await service.run_daily_prediction_delivery(555)

        self.assertEqual(first.outcome, DailyProductionDeliveryOutcome.partially_delivered)
        self.assertEqual(first.qualifying_delivery.status, PredictionMessageDeliveryStatus.delivered)
        self.assertEqual(first.prediction_delivery.status, PredictionMessageDeliveryStatus.failed)
        self.assertEqual(second.outcome, DailyProductionDeliveryOutcome.delivered)
        self.assertEqual(len(successful.messages), 1)
        self.assertIn('OVER 1.40–1.50 PREDICTIONS', successful.messages[0]['text'])
        self.assertEqual(booking.calls, 1)
        self.assertEqual(retry_booking.calls, 0)

    async def test_qualifying_failure_retries_only_qualifying_message(self):
        failing = RecordingTelegramTransport(
            lambda payload: 'OVER 1.40–1.50 GAMES' in payload['text']
        )
        service, _ = self.service(production_result(), failing)
        first = await service.run_daily_prediction_delivery(555)

        successful = RecordingTelegramTransport()
        service, _ = self.service(production_result(), successful)
        second = await service.run_daily_prediction_delivery(555)

        self.assertEqual(first.outcome, DailyProductionDeliveryOutcome.partially_delivered)
        self.assertEqual(first.qualifying_delivery.status, PredictionMessageDeliveryStatus.failed)
        self.assertEqual(first.prediction_delivery.status, PredictionMessageDeliveryStatus.delivered)
        self.assertEqual(second.outcome, DailyProductionDeliveryOutcome.delivered)
        self.assertEqual(len(successful.messages), 1)
        self.assertIn('OVER 1.40–1.50 GAMES', successful.messages[0]['text'])

    async def test_both_failures_retry_both_messages_without_rebooking(self):
        failing = RecordingTelegramTransport(lambda payload: True)
        service, booking = self.service(production_result(), failing)
        first = await service.run_daily_prediction_delivery(555)

        successful = RecordingTelegramTransport()
        service, retry_booking = self.service(production_result(), successful)
        second = await service.run_daily_prediction_delivery(555)

        self.assertEqual(first.outcome, DailyProductionDeliveryOutcome.failed)
        self.assertEqual(len(failing.messages), 0)
        self.assertEqual(second.outcome, DailyProductionDeliveryOutcome.delivered)
        self.assertEqual(len(successful.messages), 2)
        self.assertEqual(booking.calls, 1)
        self.assertEqual(retry_booking.calls, 0)

    async def test_message_too_large_fails_that_category_without_truncation(self):
        result_data = production_result()
        formatter = mock.Mock()
        formatter.build_qualifying_payload.side_effect = TelegramPredictionMessageTooLarge(
            required_utf16_length=TELEGRAM_MESSAGE_SAFE_UTF16_LIMIT + 1,
            limit=TELEGRAM_MESSAGE_SAFE_UTF16_LIMIT,
            qualifying_count=100,
            prediction_count=0,
        )
        formatter.build_prediction_payload.return_value = {
            'chat_id': 555,
            'text': 'prediction message',
        }
        transport = RecordingTelegramTransport()
        service = DailyPredictionProductionService(
            evaluation_provider=FakeEvaluationProvider(),
            booking_service=FakeBookingService(result_data),
            telegram_transport=transport,
            store=self.store,
            clock=lambda: NIGERIA_MIDNIGHT_UTC,
            formatter=formatter,
        )

        result = await service.run_daily_prediction_delivery(555)

        self.assertEqual(result.outcome, DailyProductionDeliveryOutcome.partially_delivered)
        self.assertEqual(result.qualifying_delivery.status, PredictionMessageDeliveryStatus.failed)
        self.assertIn('Message too large', result.qualifying_delivery.error_summary)
        self.assertEqual(result.prediction_delivery.status, PredictionMessageDeliveryStatus.delivered)
        self.assertEqual(len(transport.messages), 1)

    async def test_oversized_prediction_message_fails_without_truncation(self):
        result_data = production_result()
        formatter = mock.Mock()
        formatter.build_qualifying_payload.return_value = {
            'chat_id': 555,
            'text': 'qualifying message',
        }
        formatter.build_prediction_payload.side_effect = TelegramPredictionMessageTooLarge(
            required_utf16_length=TELEGRAM_MESSAGE_SAFE_UTF16_LIMIT + 1,
            limit=TELEGRAM_MESSAGE_SAFE_UTF16_LIMIT,
            qualifying_count=0,
            prediction_count=8,
        )
        transport = RecordingTelegramTransport()
        service = DailyPredictionProductionService(
            evaluation_provider=FakeEvaluationProvider(),
            booking_service=FakeBookingService(result_data),
            telegram_transport=transport,
            store=self.store,
            clock=lambda: NIGERIA_MIDNIGHT_UTC,
            formatter=formatter,
        )

        result = await service.run_daily_prediction_delivery(555)

        self.assertEqual(result.outcome, DailyProductionDeliveryOutcome.partially_delivered)
        self.assertEqual(result.qualifying_delivery.status, PredictionMessageDeliveryStatus.delivered)
        self.assertEqual(result.prediction_delivery.status, PredictionMessageDeliveryStatus.failed)
        self.assertIn('Message too large', result.prediction_delivery.error_summary)
        self.assertEqual(len(transport.messages), 1)

    async def test_new_day_preserves_previous_generation(self):
        first_transport = RecordingTelegramTransport()
        first_service, _ = self.service(production_result(), first_transport)
        first = await first_service.run_daily_prediction_delivery(555)

        second_transport = RecordingTelegramTransport()
        second_service, _ = self.service(
            production_result(today_predictions=1, tomorrow_predictions=1),
            second_transport,
            now=NIGERIA_MIDNIGHT_UTC + timedelta(days=1),
        )
        second = await second_service.run_daily_prediction_delivery(555)

        self.assertNotEqual(first.generation_identity, second.generation_identity)
        self.assertEqual(str(first.today), '2026-09-13')
        self.assertEqual(str(second.today), '2026-09-14')
        self.assertEqual(len(first_transport.messages), 2)
        self.assertEqual(len(second_transport.messages), 2)

    async def test_historical_prediction_and_settlement_fields_remain_unchanged(self):
        kickoff = NIGERIA_MIDNIGHT_UTC + timedelta(hours=2)
        evaluation = evaluate_prediction(
            make_candidate(event_id='sr:match:historical', kickoff=kickoff)
        )
        self.store.save_predictions([evaluation])
        self.store.apply_settlement_update(
            PredictionSettlementUpdate(
                prediction_id=self.store.get_prediction(evaluation.identity).id,
                event_id=evaluation.identity.event_id,
                prediction_status=PredictionStatus.settled_win,
                live_status='Ended',
                current_home_goals=2,
                current_away_goals=1,
                actual_goals=3,
                actual_result='win',
                settled_at=NIGERIA_MIDNIGHT_UTC + timedelta(hours=4),
            )
        )
        before = self.store.get_prediction(evaluation.identity).model_dump(mode='json')

        first_transport = RecordingTelegramTransport()
        first_service, _ = self.service(production_result(), first_transport)
        first = await first_service.run_daily_prediction_delivery(555)
        second_transport = RecordingTelegramTransport()
        second_service, _ = self.service(
            production_result(today_predictions=1, tomorrow_predictions=1),
            second_transport,
            now=NIGERIA_MIDNIGHT_UTC + timedelta(days=1),
        )
        second = await second_service.run_daily_prediction_delivery(555)

        after = self.store.get_prediction(evaluation.identity)
        self.assertEqual(first.outcome, DailyProductionDeliveryOutcome.delivered)
        self.assertEqual(second.outcome, DailyProductionDeliveryOutcome.delivered)
        self.assertEqual(after.model_dump(mode='json'), before)
        first_generation = self.store.find_generation_by_input(
            generation_identity=first.generation_identity,
            input_identity=first.input_identity,
        )
        self.assertIsNotNone(first_generation)
        self.assertIsNotNone(first_generation.result)


class MessageDeliveryClaimTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix='.sqlite3')
        os.close(handle)
        self.store = PredictionStore(self.path)

    def tearDown(self):
        os.unlink(self.path)

    def test_claim_is_idempotent_and_supports_retry(self):
        now = NIGERIA_MIDNIGHT_UTC
        first, claimed = self.store.claim_message_delivery(
            generation_identity='generation',
            category=PredictionMessageCategory.qualifying,
            delivery_identity='delivery',
            input_identity='input',
            batch_identities=['batch'],
            booking_codes=['CODE'],
            payload={'text': 'message'},
            now=now,
            ttl_seconds=60,
        )
        second, second_claimed = self.store.claim_message_delivery(
            generation_identity='generation',
            category=PredictionMessageCategory.qualifying,
            delivery_identity='delivery',
            input_identity='input',
            batch_identities=['batch'],
            booking_codes=['CODE'],
            payload={'text': 'message'},
            now=now + timedelta(seconds=1),
            ttl_seconds=60,
        )
        self.assertTrue(claimed)
        self.assertFalse(second_claimed)
        self.assertEqual(second.status, PredictionMessageDeliveryStatus.pending)

        failed = self.store.mark_message_delivery_failed(
            'delivery',
            now=now + timedelta(seconds=2),
            error_summary='Telegram delivery failed: RuntimeError',
        )
        retried, retry_claimed = self.store.claim_message_delivery(
            generation_identity='generation',
            category=PredictionMessageCategory.qualifying,
            delivery_identity='delivery',
            input_identity='input',
            batch_identities=['batch'],
            booking_codes=['CODE'],
            payload={'text': 'message'},
            now=now + timedelta(seconds=3),
            ttl_seconds=60,
        )
        delivered = self.store.mark_message_delivery_delivered(
            'delivery',
            sent_at=now + timedelta(seconds=4),
        )
        self.assertEqual(failed.status, PredictionMessageDeliveryStatus.failed)
        self.assertTrue(retry_claimed)
        self.assertEqual(retried.retry_count, 1)
        self.assertEqual(retried.payload, {'text': 'message'})
        self.assertEqual(delivered.status, PredictionMessageDeliveryStatus.delivered)


class ConcurrentProductionDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix='.sqlite3')
        os.close(handle)
        self.store = PredictionStore(self.path)

    def tearDown(self):
        os.unlink(self.path)

    async def test_concurrent_generation_delivers_two_messages_once(self):
        entered = asyncio.Event()
        release = asyncio.Event()
        result_data = production_result()

        class BlockingBookingService(FakeBookingService):
            async def create_booking_pools(self, *args: Any, **kwargs: Any) -> Any:
                await super().create_booking_pools(*args, **kwargs)
                entered.set()
                await release.wait()
                return result_data

        booking = BlockingBookingService(result_data)
        transport = RecordingTelegramTransport()
        service = DailyPredictionProductionService(
            evaluation_provider=FakeEvaluationProvider(),
            booking_service=booking,
            telegram_transport=transport,
            store=self.store,
            clock=lambda: NIGERIA_MIDNIGHT_UTC,
        )

        first_task = asyncio.create_task(service.run_daily_prediction_delivery(555))
        await entered.wait()
        second = await service.run_daily_prediction_delivery(555)
        self.assertEqual(second.outcome, DailyProductionDeliveryOutcome.in_progress)
        self.assertEqual(len(transport.messages), 0)

        release.set()
        first = await first_task
        self.assertEqual(first.outcome, DailyProductionDeliveryOutcome.delivered)
        self.assertEqual(booking.calls, 1)
        self.assertEqual(len(transport.messages), 2)


if __name__ == '__main__':
    unittest.main()
