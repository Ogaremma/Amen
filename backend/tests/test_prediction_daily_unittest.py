from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from app.schemas.prediction_booking import (
    PredictionBookingBatchStatus,
    PredictionBookingDay,
    PredictionBookingDayStatus,
    PredictionBookingPool,
    PredictionBookingResultStatus,
)
from app.schemas.prediction_daily import (
    DailyPredictionDeliveryOutcome,
    PredictionGenerationStatus,
)
from app.services.prediction_daily import (
    DailyPredictionDeliveryService,
    PREDICTION_TIMEZONE_NAME,
    SportyBetEvidenceEvaluationProvider,
    daily_prediction_window,
    prediction_input_identity,
    prediction_generation_identity,
)
from app.services.prediction_store import PredictionStore
from app.services.telegram_prediction_delivery import TelegramPredictionDeliveryService
from backend.tests.test_prediction_booking_unittest import (
    TODAY_KICKOFF,
    candidate,
    catalogue,
    selected_evaluation,
)
from backend.tests.test_telegram_prediction_delivery_unittest import (
    NOW,
    TODAY,
    TOMORROW,
    batch,
    day_result,
    empty_result,
    explanation,
    result_with,
    selection,
)


SEP_12_MORNING = datetime(2026, 9, 12, 9, 0, tzinfo=timezone.utc)
SEP_12_BEFORE_MIDNIGHT = datetime(2026, 9, 12, 23, 59, 59, tzinfo=timezone.utc)
SEP_13_AFTER_MIDNIGHT = datetime(2026, 9, 13, 0, 0, 1, tzinfo=timezone.utc)
SEP_13_MORNING = datetime(2026, 9, 13, 9, 0, tzinfo=timezone.utc)


def booked_result(
    *,
    qualifying_code: str = 'QUAL1',
    prediction_code: str = 'PRED1',
) -> object:
    qualifying = selection('sr:match:qualifying', TODAY, odds=1.43)
    prediction = selection(
        'sr:match:prediction',
        TODAY + timedelta(minutes=30),
        odds=1.45,
        evidence_score=0.82,
        explanation=explanation(),
    )
    return result_with(
        qualifying_today=day_result(
            PredictionBookingPool.qualifying,
            PredictionBookingDay.today,
            selections=[qualifying],
            batches=[
                batch(
                    PredictionBookingPool.qualifying,
                    PredictionBookingDay.today,
                    [qualifying],
                    code=qualifying_code,
                )
            ],
        ),
        prediction_today=day_result(
            PredictionBookingPool.predictions,
            PredictionBookingDay.today,
            selections=[prediction],
            batches=[
                batch(
                    PredictionBookingPool.predictions,
                    PredictionBookingDay.today,
                    [prediction],
                    code=prediction_code,
                )
            ],
        ),
    )


class DailyPredictionWindowTests(unittest.TestCase):
    def test_uses_explicit_utc_calendar(self):
        window = daily_prediction_window(SEP_12_MORNING)
        self.assertEqual(window.timezone_name, PREDICTION_TIMEZONE_NAME)
        self.assertEqual(str(window.today), '2026-09-12')
        self.assertEqual(str(window.tomorrow), '2026-09-13')

    def test_rolling_day_changes_after_midnight(self):
        before = daily_prediction_window(SEP_12_BEFORE_MIDNIGHT)
        after = daily_prediction_window(SEP_13_AFTER_MIDNIGHT)
        self.assertEqual((str(before.today), str(before.tomorrow)), ('2026-09-12', '2026-09-13'))
        self.assertEqual((str(after.today), str(after.tomorrow)), ('2026-09-13', '2026-09-14'))

    def test_generation_identity_is_stable_within_day_and_changes_next_day(self):
        september_12 = prediction_generation_identity(daily_prediction_window(SEP_12_MORNING))
        september_12_later = prediction_generation_identity(
            daily_prediction_window(SEP_12_BEFORE_MIDNIGHT)
        )
        september_13 = prediction_generation_identity(daily_prediction_window(SEP_13_MORNING))
        self.assertEqual(september_12, september_12_later)
        self.assertNotEqual(september_12, september_13)


class NullEvidenceProvider:
    def get_evidence(self, candidate, *, now=None):
        return None


class SportyBetEvidenceEvaluationProviderTests(unittest.TestCase):
    def test_provider_uses_injected_catalogue_and_evidence_sources(self):
        market_candidate = candidate('sr:match:provider', TODAY_KICKOFF)

        async def candidate_fetcher(**kwargs):
            return catalogue([market_candidate.fixture])

        provider = SportyBetEvidenceEvaluationProvider(
            NullEvidenceProvider(),
            candidate_fetcher=candidate_fetcher,
        )
        evaluations = asyncio.run(provider.evaluate(now=SEP_12_MORNING))

        self.assertEqual(len(evaluations), 1)
        self.assertFalse(evaluations[0].selected)
        self.assertEqual(evaluations[0].model_version, 'evidence-v1')


class PredictionGenerationStoreTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix='.sqlite3')
        os.close(handle)
        self.store = PredictionStore(self.path)

    def tearDown(self):
        os.unlink(self.path)

    def test_generation_and_delivery_state_transitions(self):
        window = daily_prediction_window(SEP_12_MORNING)
        generation_identity = prediction_generation_identity(window)
        claim, claimed = self.store.claim_generation(
            generation_identity=generation_identity,
            timezone_name=window.timezone_name,
            today=window.today,
            tomorrow=window.tomorrow,
            now=SEP_12_MORNING,
            ttl_seconds=300,
        )
        self.assertTrue(claimed)
        self.assertEqual(claim.status, PredictionGenerationStatus.generating)

        generated = self.store.mark_generation_generated(
            claim.id,
            input_identity='input-identity',
            delivery_identity='delivery-identity',
            generated_at=SEP_12_MORNING,
            result=empty_result(),
            qualifying_batch_identities=['qualifying-batch'],
            prediction_batch_identities=['prediction-batch'],
            booking_codes=['QUAL1'],
        )
        self.assertEqual(generated.status, PredictionGenerationStatus.generated)
        self.assertIsNotNone(generated.result)
        self.assertEqual(generated.booking_codes, ['QUAL1'])

        pending = self.store.mark_generation_delivery_pending(
            claim.id,
            now=SEP_12_MORNING,
        )
        self.assertEqual(pending.status, PredictionGenerationStatus.delivery_pending)

        delivered = self.store.mark_generation_delivered(
            claim.id,
            delivered_at=SEP_12_MORNING,
        )
        self.assertEqual(delivered.status, PredictionGenerationStatus.delivered)

        retry_claim, retry_claimed = self.store.claim_generation(
            generation_identity=generation_identity,
            timezone_name=window.timezone_name,
            today=window.today,
            tomorrow=window.tomorrow,
            now=SEP_12_MORNING + timedelta(minutes=1),
            ttl_seconds=300,
        )
        self.assertTrue(retry_claimed)
        retry_generated = self.store.mark_generation_generated(
            retry_claim.id,
            input_identity='input-identity-2',
            delivery_identity='delivery-identity-2',
            generated_at=SEP_12_MORNING + timedelta(minutes=1),
            result=empty_result(),
            qualifying_batch_identities=[],
            prediction_batch_identities=[],
            booking_codes=[],
        )
        retry_pending = self.store.mark_generation_delivery_pending(
            retry_claim.id,
            now=SEP_12_MORNING + timedelta(minutes=1),
        )
        failed = self.store.mark_generation_delivery_failed(
            retry_claim.id,
            now=SEP_12_MORNING + timedelta(minutes=1),
            error_summary='Telegram delivery failed: HTTPError',
        )
        self.assertEqual(retry_generated.status, PredictionGenerationStatus.generated)
        self.assertEqual(retry_pending.status, PredictionGenerationStatus.delivery_pending)
        self.assertEqual(failed.status, PredictionGenerationStatus.delivery_failed)
        self.assertIsNotNone(failed.result)


class FakeEvaluationProvider:
    def __init__(self, evaluations=None, error: Exception | None = None):
        self.evaluations = evaluations or []
        self.error = error
        self.calls = 0

    async def evaluate(self, *, now):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.evaluations


class FakeBookingService:
    def __init__(self, result, *, entered=None, release=None):
        self.result = result
        self.entered = entered
        self.release = release
        self.calls = 0

    async def create_booking_pools(self, evaluations, **kwargs):
        self.calls += 1
        if self.entered is not None:
            self.entered.set()
        if self.release is not None:
            await self.release.wait()
        return self.result


class FakeTelegramTransport:
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.calls = 0
        self.results = []

    async def send_result(self, chat_id, result):
        self.calls += 1
        if self.error is not None:
            raise self.error
        self.results.append(result)
        return result


class RecordingTelegramBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, payload):
        self.messages.append(payload)
        return {'ok': True, 'result': True}


class DailyPredictionDeliveryServiceTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix='.sqlite3')
        os.close(handle)
        self.store = PredictionStore(self.path)

    def tearDown(self):
        os.unlink(self.path)

    def _service(
        self,
        *,
        booking_result,
        evaluation_provider=None,
        telegram_transport=None,
        now=SEP_12_MORNING,
    ):
        return DailyPredictionDeliveryService(
            evaluation_provider=evaluation_provider or FakeEvaluationProvider(),
            booking_service=FakeBookingService(booking_result),
            telegram_delivery=telegram_transport or FakeTelegramTransport(),
            store=self.store,
            clock=lambda: now,
        )

    def test_first_delivery_sends_once_and_repeat_is_already_delivered(self):
        result = booked_result()
        provider = FakeEvaluationProvider()
        booking = FakeBookingService(result)
        telegram = FakeTelegramTransport()
        service = DailyPredictionDeliveryService(
            evaluation_provider=provider,
            booking_service=booking,
            telegram_delivery=telegram,
            store=self.store,
            clock=lambda: SEP_12_MORNING,
        )

        first = asyncio.run(service.generate_daily_prediction_delivery(555))
        second = asyncio.run(service.generate_daily_prediction_delivery(555))

        self.assertEqual(first.outcome, DailyPredictionDeliveryOutcome.delivered)
        self.assertEqual(second.outcome, DailyPredictionDeliveryOutcome.already_delivered)
        self.assertEqual(booking.calls, 1)
        self.assertEqual(telegram.calls, 1)
        self.assertEqual(first.delivery_identity, second.delivery_identity)
        record = self.store.find_generation_by_input(
            generation_identity=first.generation_identity,
            input_identity=first.input_identity,
        )
        self.assertEqual(record.status, PredictionGenerationStatus.delivered)

    def test_failed_telegram_delivery_can_retry_without_new_booking(self):
        result = booked_result()
        booking = FakeBookingService(result)
        failing_telegram = FakeTelegramTransport(RuntimeError('network unavailable'))
        service = DailyPredictionDeliveryService(
            evaluation_provider=FakeEvaluationProvider(),
            booking_service=booking,
            telegram_delivery=failing_telegram,
            store=self.store,
            clock=lambda: SEP_12_MORNING,
        )

        failed = asyncio.run(service.generate_daily_prediction_delivery(555))
        self.assertEqual(failed.outcome, DailyPredictionDeliveryOutcome.failed)
        self.assertEqual(failed.state, PredictionGenerationStatus.delivery_failed)
        self.assertIsNotNone(failed.result)

        successful_telegram = FakeTelegramTransport()
        retry_service = DailyPredictionDeliveryService(
            evaluation_provider=FakeEvaluationProvider(),
            booking_service=booking,
            telegram_delivery=successful_telegram,
            store=self.store,
            clock=lambda: SEP_12_MORNING + timedelta(minutes=1),
        )
        retried = asyncio.run(retry_service.generate_daily_prediction_delivery(555))

        self.assertEqual(retried.outcome, DailyPredictionDeliveryOutcome.delivered)
        self.assertEqual(booking.calls, 1)
        self.assertEqual(successful_telegram.calls, 1)
        self.assertEqual(retried.delivery_identity, failed.delivery_identity)
        self.assertEqual(retried.result.qualifying.today.booking_codes, ['QUAL1'])
        self.assertEqual(retried.result.predictions.today.booking_codes, ['PRED1'])

    def test_expired_claim_with_generated_result_resends_without_rebooking(self):
        window = daily_prediction_window(SEP_12_MORNING)
        generation_identity = prediction_generation_identity(window)
        input_identity = prediction_input_identity(generation_identity, [])
        claim, claimed = self.store.claim_generation(
            generation_identity=generation_identity,
            timezone_name=window.timezone_name,
            today=window.today,
            tomorrow=window.tomorrow,
            now=SEP_12_MORNING,
            ttl_seconds=300,
        )
        self.assertTrue(claimed)
        self.store.mark_generation_generated(
            claim.id,
            input_identity=input_identity,
            delivery_identity='stored-delivery',
            generated_at=SEP_12_MORNING,
            result=booked_result(qualifying_code='REUSE-Q', prediction_code='REUSE-P'),
            qualifying_batch_identities=['stored-qualifying'],
            prediction_batch_identities=['stored-prediction'],
            booking_codes=['REUSE-Q', 'REUSE-P'],
        )

        booking = FakeBookingService(empty_result())
        telegram = FakeTelegramTransport()
        service = DailyPredictionDeliveryService(
            evaluation_provider=FakeEvaluationProvider(),
            booking_service=booking,
            telegram_delivery=telegram,
            store=self.store,
            clock=lambda: SEP_12_MORNING + timedelta(seconds=301),
        )
        result = asyncio.run(service.generate_daily_prediction_delivery(555))

        self.assertEqual(result.outcome, DailyPredictionDeliveryOutcome.delivered)
        self.assertEqual(booking.calls, 0)
        self.assertEqual(telegram.calls, 1)
        self.assertEqual(result.result.qualifying.today.booking_codes, ['REUSE-Q'])
        self.assertEqual(result.result.predictions.today.booking_codes, ['REUSE-P'])

    def test_generation_failure_does_not_call_booking_or_telegram(self):
        provider = FakeEvaluationProvider(error=RuntimeError('evidence unavailable'))
        booking = FakeBookingService(booked_result())
        telegram = FakeTelegramTransport()
        service = DailyPredictionDeliveryService(
            evaluation_provider=provider,
            booking_service=booking,
            telegram_delivery=telegram,
            store=self.store,
            clock=lambda: SEP_12_MORNING,
        )

        result = asyncio.run(service.generate_daily_prediction_delivery(555))

        self.assertEqual(result.outcome, DailyPredictionDeliveryOutcome.failed)
        self.assertEqual(result.state, PredictionGenerationStatus.generation_failed)
        self.assertEqual(booking.calls, 0)
        self.assertEqual(telegram.calls, 0)
        self.assertIsNone(result.result)

    def test_unavailable_catalogue_is_generation_failure_and_is_not_sent(self):
        unavailable = empty_result()
        unavailable.status = PredictionBookingResultStatus.unavailable
        booking = FakeBookingService(unavailable)
        telegram = FakeTelegramTransport()
        service = DailyPredictionDeliveryService(
            evaluation_provider=FakeEvaluationProvider(),
            booking_service=booking,
            telegram_delivery=telegram,
            store=self.store,
            clock=lambda: SEP_12_MORNING,
        )

        result = asyncio.run(service.generate_daily_prediction_delivery(555))

        self.assertEqual(result.outcome, DailyPredictionDeliveryOutcome.failed)
        self.assertEqual(result.state, PredictionGenerationStatus.generation_failed)
        self.assertEqual(telegram.calls, 0)

    def test_rolling_to_next_day_preserves_historical_prediction_records(self):
        day_one = selected_evaluation(candidate('sr:match:day-one', TODAY_KICKOFF))
        day_two = selected_evaluation(candidate('sr:match:day-two', TOMORROW))
        self.store.save_predictions([day_one])
        before = self.store.list_predictions()
        self.assertEqual(len(before), 1)

        service = self._service(booking_result=empty_result(), now=SEP_13_MORNING)
        result = asyncio.run(service.generate_daily_prediction_delivery(555))
        self.store.save_predictions([day_two])
        after = self.store.list_predictions()

        self.assertEqual(result.outcome, DailyPredictionDeliveryOutcome.delivered)
        self.assertEqual({record.event_id for record in after}, {
            'sr:match:day-one',
            'sr:match:day-two',
        })
        self.assertEqual(after[0].updated_at, before[0].updated_at)

    def test_empty_pool_message_is_delivered_with_phase_4e_structure(self):
        bot = RecordingTelegramBot()
        telegram_delivery = TelegramPredictionDeliveryService(bot)
        service = DailyPredictionDeliveryService(
            evaluation_provider=FakeEvaluationProvider(),
            booking_service=FakeBookingService(empty_result()),
            telegram_delivery=telegram_delivery,
            store=self.store,
            clock=lambda: SEP_12_MORNING,
        )

        result = asyncio.run(service.generate_daily_prediction_delivery(555))

        self.assertEqual(result.outcome, DailyPredictionDeliveryOutcome.delivered)
        self.assertEqual(len(bot.messages), 1)
        text = bot.messages[0]['text']
        self.assertEqual(text.count('No qualifying games currently available.'), 2)
        self.assertEqual(
            text.count('No model-selected predictions meet the current evidence threshold.'),
            2,
        )
        self.assertNotIn('reply_markup', bot.messages[0])

    def test_partial_booking_failure_is_preserved_in_daily_delivery(self):
        successful = selection('sr:match:success', TODAY)
        failed = selection('sr:match:failed', TODAY + timedelta(minutes=15))
        partial = result_with(
            qualifying_today=day_result(
                PredictionBookingPool.qualifying,
                PredictionBookingDay.today,
                selections=[successful, failed],
                batches=[
                    batch(
                        PredictionBookingPool.qualifying,
                        PredictionBookingDay.today,
                        [successful],
                        code='GOOD1',
                    ),
                    batch(
                        PredictionBookingPool.qualifying,
                        PredictionBookingDay.today,
                        [failed],
                        index=2,
                        status=PredictionBookingBatchStatus.failed,
                        error='provider failure',
                    ),
                ],
                status=PredictionBookingDayStatus.partial,
            ),
            prediction_today=day_result(
                PredictionBookingPool.predictions,
                PredictionBookingDay.today,
            ),
        )
        bot = RecordingTelegramBot()
        telegram_delivery = TelegramPredictionDeliveryService(bot)
        service = DailyPredictionDeliveryService(
            evaluation_provider=FakeEvaluationProvider(),
            booking_service=FakeBookingService(partial),
            telegram_delivery=telegram_delivery,
            store=self.store,
            clock=lambda: SEP_12_MORNING,
        )

        result = asyncio.run(service.generate_daily_prediction_delivery(555))

        self.assertEqual(result.outcome, DailyPredictionDeliveryOutcome.delivered)
        self.assertIn(
            'Booking partly unavailable; 1 of 2 booking batches succeeded.',
            bot.messages[0]['text'],
        )
        buttons = [
            button
            for row in bot.messages[0]['reply_markup']['inline_keyboard']
            for button in row
        ]
        self.assertEqual(len(buttons), 1)
        self.assertEqual(buttons[0]['copy_text']['text'], 'GOOD1')


class ConcurrentDailyPredictionDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix='.sqlite3')
        os.close(handle)
        self.store = PredictionStore(self.path)

    def tearDown(self):
        os.unlink(self.path)

    async def test_concurrent_duplicate_generation_delivers_once(self):
        entered = asyncio.Event()
        release = asyncio.Event()
        booking = FakeBookingService(booked_result(), entered=entered, release=release)
        telegram = FakeTelegramTransport()
        service = DailyPredictionDeliveryService(
            evaluation_provider=FakeEvaluationProvider(),
            booking_service=booking,
            telegram_delivery=telegram,
            store=self.store,
            clock=lambda: SEP_12_MORNING,
        )

        first_task = asyncio.create_task(service.generate_daily_prediction_delivery(555))
        await entered.wait()
        second = await service.generate_daily_prediction_delivery(555)
        self.assertEqual(second.outcome, DailyPredictionDeliveryOutcome.in_progress)
        self.assertEqual(telegram.calls, 0)

        release.set()
        first = await first_task

        self.assertEqual(first.outcome, DailyPredictionDeliveryOutcome.delivered)
        self.assertEqual(booking.calls, 1)
        self.assertEqual(telegram.calls, 1)


if __name__ == '__main__':
    unittest.main()
