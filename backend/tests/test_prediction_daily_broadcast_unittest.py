from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime
from typing import Any

import httpx

from app.schemas.prediction_daily_broadcast import TelegramRecipient
from app.schemas.prediction_daily_production import (
    DailyProductionDeliveryOutcome,
    PredictionMessageCategory,
    PredictionMessageDeliveryStatus,
)
from app.services.prediction_daily_production import DailyPredictionProductionService
from app.services.prediction_store import PredictionStore
from app.services.telegram_user_store import TelegramUserStore
from backend.tests.test_prediction_daily_production_unittest import (
    NIGERIA_MIDNIGHT_UTC,
    FakeBookingService,
    FakeEvaluationProvider,
    production_result,
)


def permanent_telegram_error() -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://api.telegram.org")
    response = httpx.Response(
        403,
        request=request,
        json={
            "ok": False,
            "description": "Forbidden: bot was blocked by the user",
        },
    )
    return httpx.HTTPStatusError("Forbidden", request=request, response=response)


class BroadcastTransport:
    def __init__(self):
        self.messages: list[dict[str, Any]] = []
        self.failures: dict[tuple[int | str, str], Exception] = {}

    @staticmethod
    def category(payload: dict[str, Any]) -> str:
        return (
            PredictionMessageCategory.predictions.value
            if "PREDICTIONS" in payload["text"]
            else PredictionMessageCategory.qualifying.value
        )

    async def send_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        key = (payload["chat_id"], self.category(payload))
        failure = self.failures.get(key)
        if failure is not None:
            raise failure
        self.messages.append(payload)
        return {"ok": True}


class DailyPredictionBroadcastTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        self.store = PredictionStore(self.path)
        self.user_store = TelegramUserStore(self.path)
        self.transport = BroadcastTransport()

    def tearDown(self):
        os.unlink(self.path)

    def service(self, result: Any) -> tuple[DailyPredictionProductionService, FakeBookingService]:
        booking = FakeBookingService(result)
        service = DailyPredictionProductionService(
            evaluation_provider=FakeEvaluationProvider(),
            booking_service=booking,
            telegram_transport=self.transport,
            store=self.store,
            telegram_user_store=self.user_store,
            clock=lambda: NIGERIA_MIDNIGHT_UTC,
        )
        return service, booking

    def recipients(self) -> list[TelegramRecipient]:
        return [
            TelegramRecipient(
                telegram_user_id=user.telegram_user_id,
                telegram_chat_id=user.telegram_chat_id,
            )
            for user in self.user_store.list_eligible_users()
        ]

    async def test_one_generation_serves_multiple_users_without_rebooking(self):
        for user_id in (1, 2, 3):
            self.user_store.upsert_from_auth(user_id)
        service, booking = self.service(production_result())

        result = await service.run_daily_prediction_broadcast(
            self.recipients(),
            now=NIGERIA_MIDNIGHT_UTC,
        )

        self.assertEqual(result.outcome, DailyProductionDeliveryOutcome.delivered)
        self.assertEqual(result.recipient_count, 3)
        self.assertEqual(booking.calls, 1)
        self.assertEqual(len(self.transport.messages), 6)
        self.assertEqual(
            [message["chat_id"] for message in self.transport.messages],
            [1, 1, 2, 2, 3, 3],
        )

    async def test_retry_failed_recipient_without_resending_successful_recipients(self):
        for user_id in (1, 2, 3):
            self.user_store.upsert_from_auth(user_id)
        service, booking = self.service(production_result())
        self.transport.failures[(2, PredictionMessageCategory.qualifying.value)] = RuntimeError(
            "telegram unavailable"
        )

        first = await service.run_daily_prediction_broadcast(
            self.recipients(),
            now=NIGERIA_MIDNIGHT_UTC,
        )
        self.assertEqual(
            first.outcome,
            DailyProductionDeliveryOutcome.partially_delivered,
        )
        self.assertEqual(booking.calls, 1)
        self.assertEqual(len(self.transport.messages), 5)

        self.transport.failures.clear()
        second = await service.run_daily_prediction_broadcast(
            self.recipients(),
            now=NIGERIA_MIDNIGHT_UTC,
        )
        self.assertEqual(second.outcome, DailyProductionDeliveryOutcome.delivered)
        self.assertEqual(booking.calls, 1)
        self.assertEqual(len(self.transport.messages), 6)
        self.assertEqual(self.transport.messages[-1]["chat_id"], 2)
        self.assertIn("OVER 1.40", self.transport.messages[-1]["text"])

    async def test_permanent_failure_marks_recipient_undeliverable(self):
        self.user_store.upsert_from_auth(1)
        self.user_store.upsert_from_auth(2)
        service, booking = self.service(production_result())
        self.transport.failures[(2, PredictionMessageCategory.qualifying.value)] = (
            permanent_telegram_error()
        )

        first = await service.run_daily_prediction_broadcast(
            self.recipients(),
            now=NIGERIA_MIDNIGHT_UTC,
        )
        self.assertEqual(
            first.outcome,
            DailyProductionDeliveryOutcome.partially_delivered,
        )
        self.assertEqual(self.user_store.get(2).status, "undeliverable")
        self.assertEqual(booking.calls, 1)

        second = await service.run_daily_prediction_broadcast(
            self.recipients(),
            now=NIGERIA_MIDNIGHT_UTC,
        )
        self.assertEqual(second.outcome, DailyProductionDeliveryOutcome.delivered)
        self.assertEqual(booking.calls, 1)
        self.assertNotIn(2, [message["chat_id"] for message in self.transport.messages])

    async def test_empty_prediction_pool_remains_honest(self):
        self.user_store.upsert_from_auth(1)
        service, _ = self.service(
            production_result(today_predictions=0, tomorrow_predictions=0)
        )

        result = await service.run_daily_prediction_broadcast(
            self.recipients(),
            now=NIGERIA_MIDNIGHT_UTC,
        )

        self.assertEqual(result.outcome, DailyProductionDeliveryOutcome.delivered)
        self.assertEqual(len(self.transport.messages), 2)
        self.assertIn(
            "No model-selected predictions meet the current evidence threshold.",
            self.transport.messages[1]["text"],
        )

    async def test_zero_eligible_users_still_generates_once(self):
        service, booking = self.service(production_result())

        result = await service.run_daily_prediction_broadcast(
            [],
            now=NIGERIA_MIDNIGHT_UTC,
        )

        self.assertEqual(result.outcome, DailyProductionDeliveryOutcome.delivered)
        self.assertEqual(result.recipient_count, 0)
        self.assertEqual(booking.calls, 1)
        self.assertEqual(len(self.transport.messages), 0)

    async def test_delivery_is_idempotent_for_multiple_users(self):
        self.user_store.upsert_from_auth(1)
        self.user_store.upsert_from_auth(2)
        service, booking = self.service(production_result())

        first = await service.run_daily_prediction_broadcast(
            self.recipients(),
            now=NIGERIA_MIDNIGHT_UTC,
        )
        second = await service.run_daily_prediction_broadcast(
            self.recipients(),
            now=NIGERIA_MIDNIGHT_UTC,
        )

        self.assertEqual(first.outcome, DailyProductionDeliveryOutcome.delivered)
        self.assertEqual(second.outcome, DailyProductionDeliveryOutcome.delivered)
        self.assertEqual(booking.calls, 1)
        self.assertEqual(len(self.transport.messages), 4)
        self.assertEqual(
            [recipient.outcome for recipient in second.recipients],
            [
                DailyProductionDeliveryOutcome.already_delivered,
                DailyProductionDeliveryOutcome.already_delivered,
            ],
        )

    async def test_existing_single_recipient_api_remains_intact(self):
        service, booking = self.service(production_result())

        result = await service.run_daily_prediction_delivery(
            555,
            now=NIGERIA_MIDNIGHT_UTC,
        )

        self.assertEqual(result.outcome, DailyProductionDeliveryOutcome.delivered)
        self.assertEqual(booking.calls, 1)
        self.assertEqual(len(self.transport.messages), 2)
        self.assertEqual(
            result.qualifying_delivery.status,
            PredictionMessageDeliveryStatus.delivered,
        )
        self.assertEqual(
            result.prediction_delivery.status,
            PredictionMessageDeliveryStatus.delivered,
        )


if __name__ == "__main__":
    unittest.main()
