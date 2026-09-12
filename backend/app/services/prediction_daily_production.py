from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Protocol

import httpx

from app.schemas.prediction_daily_broadcast import (
    DailyPredictionBroadcastResult,
    PredictionRecipientDeliveryResult,
    TelegramRecipient,
)
from app.schemas.prediction_booking import PredictionBookingResult
from app.schemas.prediction_daily import PredictionGenerationDeliveryRecord
from app.schemas.prediction_daily import PredictionGenerationStatus
from app.schemas.prediction_daily_production import (
    DailyPredictionProductionResult,
    DailyProductionDeliveryOutcome,
    PredictionMessageCategory,
    PredictionMessageDeliveryRecord,
    PredictionMessageDeliveryStatus,
)
from app.services.prediction_booking import PredictionBookingService
from app.services.prediction_daily import (
    PredictionEvaluationProvider,
    prediction_generation_identity,
    prediction_input_identity,
)
from app.services.prediction_daily import DailyPredictionWindow
from app.services.prediction_production_time import (
    PRODUCTION_PREDICTION_TIMEZONE,
    production_daily_prediction_window,
)
from app.services.prediction_store import PredictionStore, prediction_store
from app.services.telegram_user_store import TelegramUserStore
from app.telegram.prediction_formatter import (
    TelegramPredictionFormatter,
    TelegramPredictionMessageTooLarge,
)


logger = logging.getLogger('amen.prediction_daily_production')

DEFAULT_GENERATION_CLAIM_TTL_SECONDS = 300.0
DEFAULT_MESSAGE_DELIVERY_CLAIM_TTL_SECONDS = 120.0
PERMANENT_TELEGRAM_FAILURE_MARKERS = (
    'bot was blocked',
    'blocked by user',
    'chat not found',
    'user is deactivated',
)


def _is_permanent_telegram_failure(exc: Exception) -> bool:
    if not isinstance(exc, httpx.HTTPStatusError):
        return False
    description = exc.response.text.lower()
    return (
        exc.response.status_code in {400, 403}
        and any(marker in description for marker in PERMANENT_TELEGRAM_FAILURE_MARKERS)
    )


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _hash_value(value: dict[str, Any]) -> str:
    serialized = json.dumps(
        value,
        separators=(',', ':'),
        ensure_ascii=True,
        sort_keys=True,
    )
    return hashlib.sha256(serialized.encode('utf-8')).hexdigest()


def _category_batches(
    result: PredictionBookingResult,
    category: PredictionMessageCategory,
) -> list[str]:
    if category == PredictionMessageCategory.qualifying:
        return [
            batch.batch_identity
            for day in (result.qualifying.today, result.qualifying.tomorrow)
            for batch in day.batches
        ]
    return [
        batch.batch_identity
        for day in (result.predictions.today, result.predictions.tomorrow)
        for group in day.groups
        for batch in group.batches
    ]


def _category_booking_codes(
    result: PredictionBookingResult,
    category: PredictionMessageCategory,
) -> list[str]:
    if category == PredictionMessageCategory.qualifying:
        return [
            code
            for day in (result.qualifying.today, result.qualifying.tomorrow)
            for code in day.booking_codes
        ]
    return [
        code
        for day in (result.predictions.today, result.predictions.tomorrow)
        for group in day.groups
        for code in group.booking_codes
    ]


def prediction_message_delivery_identity(
    *,
    generation_identity: str,
    input_identity: str,
    category: PredictionMessageCategory,
    result: PredictionBookingResult,
    recipient_id: int | str | None = None,
) -> str:
    identity = {
        'generation_identity': generation_identity,
        'input_identity': input_identity,
        'category': category.value,
        'batch_identities': _category_batches(result, category),
        'booking_codes': _category_booking_codes(result, category),
    }
    if recipient_id is not None:
        identity['recipient_id'] = str(recipient_id)
    return _hash_value(identity)


def production_delivery_identity(
    qualifying_identity: str,
    prediction_identity: str,
) -> str:
    return _hash_value(
        {
            'qualifying_identity': qualifying_identity,
            'prediction_identity': prediction_identity,
        }
    )


class TelegramMessageTransport(Protocol):
    async def send_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class _GenerationPreparation:
    record: PredictionGenerationDeliveryRecord
    window: DailyPredictionWindow
    generation_identity: str
    input_identity: str


class DailyPredictionProductionService:
    def __init__(
        self,
        *,
        evaluation_provider: PredictionEvaluationProvider,
        booking_service: PredictionBookingService,
        telegram_transport: TelegramMessageTransport,
        store: PredictionStore | None = None,
        telegram_user_store: TelegramUserStore | None = None,
        clock: Callable[[], datetime] | None = None,
        formatter: TelegramPredictionFormatter | None = None,
        generation_claim_ttl_seconds: float = DEFAULT_GENERATION_CLAIM_TTL_SECONDS,
        claim_ttl_seconds: float = DEFAULT_MESSAGE_DELIVERY_CLAIM_TTL_SECONDS,
    ) -> None:
        self.evaluation_provider = evaluation_provider
        self.booking_service = booking_service
        self.telegram_transport = telegram_transport
        self.store = store or prediction_store
        self.telegram_user_store = telegram_user_store
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.formatter = formatter or TelegramPredictionFormatter()
        self.generation_claim_ttl_seconds = generation_claim_ttl_seconds
        self.claim_ttl_seconds = claim_ttl_seconds

    async def run_daily_prediction_delivery(
        self,
        chat_id: int | str,
        *,
        now: datetime | None = None,
        page_size: int | None = None,
        max_pages: int | None = None,
    ) -> DailyPredictionProductionResult:
        current = _utc(now or self.clock())
        preparation = await self._prepare_generation(
            now=current,
            page_size=page_size,
            max_pages=max_pages,
        )
        if not self._is_generation_ready(preparation.record):
            return self._preparation_result(preparation)

        recipient = TelegramRecipient(
            telegram_user_id=chat_id,
            telegram_chat_id=chat_id if isinstance(chat_id, int) else None,
        )
        self.store.mark_generation_delivery_pending(preparation.record.id, now=current)
        recipient_result = await self._deliver_recipient(
            preparation.record,
            recipient=recipient,
            now=current,
        )
        updated, _ = self._finalize_generation(
            preparation.record,
            recipient_results=[recipient_result],
            now=current,
        )
        return self._result(
            outcome=recipient_result.outcome,
            window=preparation.window,
            generation_identity=preparation.generation_identity,
            input_identity=preparation.input_identity,
            record=updated,
            qualifying_delivery=recipient_result.qualifying_delivery,
            prediction_delivery=recipient_result.prediction_delivery,
        )

    async def run_daily_prediction_broadcast(
        self,
        recipients: list[TelegramRecipient],
        *,
        now: datetime | None = None,
        page_size: int | None = None,
        max_pages: int | None = None,
    ) -> DailyPredictionBroadcastResult:
        current = _utc(now or self.clock())
        preparation = await self._prepare_generation(
            now=current,
            page_size=page_size,
            max_pages=max_pages,
        )
        if not self._is_generation_ready(preparation.record):
            return self._preparation_broadcast_result(preparation, recipients=[])

        unique_recipients: dict[int | str, TelegramRecipient] = {}
        for recipient in recipients:
            unique_recipients.setdefault(recipient.telegram_user_id, recipient)

        self.store.mark_generation_delivery_pending(preparation.record.id, now=current)
        recipient_results = [
            await self._deliver_recipient(
                preparation.record,
                recipient=recipient,
                now=current,
            )
            for recipient in unique_recipients.values()
        ]
        updated, outcome = self._finalize_generation(
            preparation.record,
            recipient_results=recipient_results,
            now=current,
        )
        return self._broadcast_result(
            outcome=outcome,
            preparation=preparation,
            record=updated,
            recipient_results=recipient_results,
        )

    async def _prepare_generation(
        self,
        *,
        now: datetime,
        page_size: int | None,
        max_pages: int | None,
    ) -> _GenerationPreparation:
        window = production_daily_prediction_window(now)
        generation_identity = prediction_generation_identity(window)
        input_identity = ''

        claim, claimed = self.store.claim_generation(
            generation_identity=generation_identity,
            timezone_name=window.timezone_name,
            today=window.today,
            tomorrow=window.tomorrow,
            now=now,
            ttl_seconds=self.generation_claim_ttl_seconds,
        )
        if not claimed:
            return _GenerationPreparation(
                record=claim,
                window=window,
                generation_identity=generation_identity,
                input_identity=claim.input_identity or '',
            )
        if claim.result is not None and claim.input_identity is not None:
            return _GenerationPreparation(
                record=claim,
                window=window,
                generation_identity=generation_identity,
                input_identity=claim.input_identity,
            )

        try:
            evaluations = await self.evaluation_provider.evaluate(now=now)
            input_identity = prediction_input_identity(generation_identity, evaluations)
            existing = self.store.find_generation_by_input(
                generation_identity=generation_identity,
                input_identity=input_identity,
            )
            if existing is not None and existing.result is not None:
                self.store.discard_generation_claim(claim.id)
                return _GenerationPreparation(
                    record=existing,
                    window=window,
                    generation_identity=generation_identity,
                    input_identity=input_identity,
                )

            booking_result = await self.booking_service.create_booking_pools(
                evaluations,
                now=now,
                page_size=page_size,
                max_pages=max_pages,
                window=window.window,
                day_timezone=PRODUCTION_PREDICTION_TIMEZONE,
                prediction_groups=True,
            )
            if booking_result.status.value == 'unavailable':
                failed = self.store.mark_generation_failed(
                    claim.id,
                    now=now,
                    error_summary='SportyBet catalogue unavailable',
                )
                return _GenerationPreparation(
                    record=failed,
                    window=window,
                    generation_identity=generation_identity,
                    input_identity=input_identity,
                )

            qualifying_identity = prediction_message_delivery_identity(
                generation_identity=generation_identity,
                input_identity=input_identity,
                category=PredictionMessageCategory.qualifying,
                result=booking_result,
            )
            prediction_identity = prediction_message_delivery_identity(
                generation_identity=generation_identity,
                input_identity=input_identity,
                category=PredictionMessageCategory.predictions,
                result=booking_result,
            )
            generated = self.store.mark_generation_generated(
                claim.id,
                input_identity=input_identity,
                delivery_identity=production_delivery_identity(
                    qualifying_identity,
                    prediction_identity,
                ),
                generated_at=now,
                result=booking_result,
                qualifying_batch_identities=_category_batches(
                    booking_result,
                    PredictionMessageCategory.qualifying,
                ),
                prediction_batch_identities=_category_batches(
                    booking_result,
                    PredictionMessageCategory.predictions,
                ),
                booking_codes=(
                    _category_booking_codes(
                        booking_result,
                        PredictionMessageCategory.qualifying,
                    )
                    + _category_booking_codes(
                        booking_result,
                        PredictionMessageCategory.predictions,
                    )
                ),
            )
            return _GenerationPreparation(
                record=generated,
                window=window,
                generation_identity=generation_identity,
                input_identity=input_identity,
            )
        except Exception as exc:
            logger.warning(
                'Daily production prediction generation failed: %s',
                type(exc).__name__,
            )
            failed = self.store.mark_generation_failed(
                claim.id,
                now=now,
                error_summary=f'Prediction generation failed: {type(exc).__name__}',
            )
            return _GenerationPreparation(
                record=failed,
                window=window,
                generation_identity=generation_identity,
                input_identity=input_identity,
            )

    @staticmethod
    def _is_generation_ready(record: PredictionGenerationDeliveryRecord) -> bool:
        return (
            record.status != PredictionGenerationStatus.generation_failed
            and record.result is not None
            and record.input_identity is not None
        )

    def _preparation_result(
        self,
        preparation: _GenerationPreparation,
    ) -> DailyPredictionProductionResult:
        outcome = (
            DailyProductionDeliveryOutcome.generation_failed
            if preparation.record.status == PredictionGenerationStatus.generation_failed
            else DailyProductionDeliveryOutcome.in_progress
        )
        return self._result(
            outcome=outcome,
            window=preparation.window,
            generation_identity=preparation.generation_identity,
            input_identity=preparation.input_identity,
            record=preparation.record,
        )

    def _preparation_broadcast_result(
        self,
        preparation: _GenerationPreparation,
        *,
        recipients: list[PredictionRecipientDeliveryResult],
    ) -> DailyPredictionBroadcastResult:
        outcome = (
            DailyProductionDeliveryOutcome.generation_failed
            if preparation.record.status == PredictionGenerationStatus.generation_failed
            else DailyProductionDeliveryOutcome.in_progress
        )
        return self._broadcast_result(
            outcome=outcome,
            preparation=preparation,
            record=preparation.record,
            recipient_results=recipients,
        )

    async def _deliver_recipient(
        self,
        record: PredictionGenerationDeliveryRecord,
        *,
        recipient: TelegramRecipient,
        now: datetime,
    ) -> PredictionRecipientDeliveryResult:
        qualifying, qualifying_already = await self._deliver_category(
            record,
            category=PredictionMessageCategory.qualifying,
            recipient=recipient,
            now=now,
        )
        if qualifying.status == PredictionMessageDeliveryStatus.undeliverable:
            return PredictionRecipientDeliveryResult(
                recipient=recipient,
                outcome=DailyProductionDeliveryOutcome.failed,
                qualifying_delivery=qualifying,
                prediction_delivery=None,
            )
        prediction, prediction_already = await self._deliver_category(
            record,
            category=PredictionMessageCategory.predictions,
            recipient=recipient,
            now=now,
        )
        outcome = self._recipient_outcome(
            qualifying=qualifying,
            prediction=prediction,
            qualifying_already=qualifying_already,
            prediction_already=prediction_already,
        )
        return PredictionRecipientDeliveryResult(
            recipient=recipient,
            outcome=outcome,
            qualifying_delivery=qualifying,
            prediction_delivery=prediction,
        )

    @staticmethod
    def _recipient_outcome(
        *,
        qualifying: PredictionMessageDeliveryRecord,
        prediction: PredictionMessageDeliveryRecord,
        qualifying_already: bool,
        prediction_already: bool,
    ) -> DailyProductionDeliveryOutcome:
        statuses = {qualifying.status, prediction.status}
        if statuses == {PredictionMessageDeliveryStatus.delivered}:
            return (
                DailyProductionDeliveryOutcome.already_delivered
                if qualifying_already and prediction_already
                else DailyProductionDeliveryOutcome.delivered
            )
        if PredictionMessageDeliveryStatus.delivered in statuses:
            return DailyProductionDeliveryOutcome.partially_delivered
        if {
            PredictionMessageDeliveryStatus.failed,
            PredictionMessageDeliveryStatus.undeliverable,
        } & statuses:
            return DailyProductionDeliveryOutcome.failed
        return DailyProductionDeliveryOutcome.in_progress

    def _finalize_generation(
        self,
        record: PredictionGenerationDeliveryRecord,
        *,
        recipient_results: list[PredictionRecipientDeliveryResult],
        now: datetime,
    ) -> tuple[PredictionGenerationDeliveryRecord, DailyProductionDeliveryOutcome]:
        outcome = self._aggregate_outcome(recipient_results)
        if outcome == DailyProductionDeliveryOutcome.delivered:
            updated = self.store.mark_generation_delivered(
                record.id,
                delivered_at=now,
            )
        elif outcome == DailyProductionDeliveryOutcome.in_progress:
            updated = self.store.mark_generation_delivery_pending(
                record.id,
                now=now,
            )
        else:
            updated = self.store.mark_generation_delivery_failed(
                record.id,
                now=now,
                error_summary='One or more Telegram recipient deliveries failed',
            )
        return updated, outcome

    @staticmethod
    def _aggregate_outcome(
        recipient_results: list[PredictionRecipientDeliveryResult],
    ) -> DailyProductionDeliveryOutcome:
        if not recipient_results:
            return DailyProductionDeliveryOutcome.delivered
        if all(
            result.outcome
            in {
                DailyProductionDeliveryOutcome.delivered,
                DailyProductionDeliveryOutcome.already_delivered,
            }
            for result in recipient_results
        ):
            return DailyProductionDeliveryOutcome.delivered
        if any(
            result.outcome
            in {
                DailyProductionDeliveryOutcome.delivered,
                DailyProductionDeliveryOutcome.already_delivered,
            }
            for result in recipient_results
        ):
            return DailyProductionDeliveryOutcome.partially_delivered
        if any(
            result.outcome == DailyProductionDeliveryOutcome.in_progress
            for result in recipient_results
        ):
            return DailyProductionDeliveryOutcome.in_progress
        return DailyProductionDeliveryOutcome.failed

    def _broadcast_result(
        self,
        *,
        outcome: DailyProductionDeliveryOutcome,
        preparation: _GenerationPreparation,
        record: PredictionGenerationDeliveryRecord,
        recipient_results: list[PredictionRecipientDeliveryResult],
    ) -> DailyPredictionBroadcastResult:
        delivered_count = sum(
            result.outcome
            in {
                DailyProductionDeliveryOutcome.delivered,
                DailyProductionDeliveryOutcome.already_delivered,
            }
            for result in recipient_results
        )
        undeliverable_count = sum(
            (
                result.qualifying_delivery is not None
                and result.qualifying_delivery.status
                == PredictionMessageDeliveryStatus.undeliverable
            )
            or (
                result.prediction_delivery is not None
                and result.prediction_delivery.status
                == PredictionMessageDeliveryStatus.undeliverable
            )
            for result in recipient_results
        )
        failed_count = len(recipient_results) - delivered_count - undeliverable_count
        return DailyPredictionBroadcastResult(
            outcome=outcome,
            generation_identity=preparation.generation_identity,
            input_identity=preparation.input_identity,
            delivery_identity=record.delivery_identity,
            timezone_name=preparation.window.timezone_name,
            today=preparation.window.today,
            tomorrow=preparation.window.tomorrow,
            generated_at=record.generated_at,
            error_summary=record.error_summary,
            result=record.result,
            recipients=recipient_results,
            recipient_count=len(recipient_results),
            delivered_recipient_count=delivered_count,
            failed_recipient_count=failed_count,
            undeliverable_recipient_count=undeliverable_count,
        )

    async def _deliver_category(
        self,
        record: PredictionGenerationDeliveryRecord,
        *,
        category: PredictionMessageCategory,
        recipient: TelegramRecipient,
        now: datetime,
    ) -> tuple[PredictionMessageDeliveryRecord, bool]:
        if record.result is None or record.input_identity is None:
            raise ValueError('Cannot deliver a generation without a result')
        result = record.result
        chat_id = recipient.chat_id
        delivery_identity = prediction_message_delivery_identity(
            generation_identity=record.generation_identity,
            input_identity=record.input_identity,
            category=category,
            result=result,
            recipient_id=recipient.telegram_user_id,
        )

        try:
            if category == PredictionMessageCategory.qualifying:
                payload = self.formatter.build_qualifying_payload(chat_id, result)
            else:
                payload = self.formatter.build_prediction_payload(chat_id, result)
        except TelegramPredictionMessageTooLarge as exc:
            claimed, _ = self.store.claim_message_delivery(
                generation_identity=record.generation_identity,
                category=category,
                delivery_identity=delivery_identity,
                input_identity=record.input_identity,
                telegram_user_id=(
                    recipient.telegram_user_id
                    if isinstance(recipient.telegram_user_id, int)
                    else None
                ),
                batch_identities=_category_batches(result, category),
                booking_codes=_category_booking_codes(result, category),
                payload={},
                now=now,
                ttl_seconds=self.claim_ttl_seconds,
            )
            return self.store.mark_message_delivery_failed(
                delivery_identity,
                now=now,
                error_summary=(
                    f'Message too large: required {exc.required_utf16_length} '
                    f'UTF-16 units, limit {exc.limit}'
                ),
            ), False

        claimed, claim_success = self.store.claim_message_delivery(
            generation_identity=record.generation_identity,
            category=category,
            delivery_identity=delivery_identity,
            input_identity=record.input_identity,
            telegram_user_id=(
                recipient.telegram_user_id
                if isinstance(recipient.telegram_user_id, int)
                else None
            ),
            batch_identities=_category_batches(result, category),
            booking_codes=_category_booking_codes(result, category),
            payload=payload,
            now=now,
            ttl_seconds=self.claim_ttl_seconds,
        )
        if not claim_success:
            return claimed, claimed.status == PredictionMessageDeliveryStatus.delivered

        try:
            await self.telegram_transport.send_message(claimed.payload)
        except Exception as exc:
            logger.warning(
                'Daily production Telegram delivery failed: %s',
                type(exc).__name__,
            )
            permanent = _is_permanent_telegram_failure(exc)
            if permanent and isinstance(recipient.telegram_user_id, int):
                if self.telegram_user_store is not None:
                    self.telegram_user_store.mark_undeliverable(
                        recipient.telegram_user_id,
                        now=now,
                    )
                return self.store.mark_message_delivery_undeliverable(
                    delivery_identity,
                    now=now,
                    error_summary=(
                        'Telegram recipient is permanently undeliverable: '
                        f'{type(exc).__name__}'
                    ),
                ), False
            return self.store.mark_message_delivery_failed(
                delivery_identity,
                now=now,
                error_summary=f'Telegram delivery failed: {type(exc).__name__}',
            ), False

        return self.store.mark_message_delivery_delivered(
            delivery_identity,
            sent_at=now,
        ), False

    @staticmethod
    def _result(
        *,
        outcome: DailyProductionDeliveryOutcome,
        window: DailyPredictionWindow,
        generation_identity: str,
        input_identity: str,
        record: PredictionGenerationDeliveryRecord,
        result: PredictionBookingResult | None = None,
        qualifying_delivery: PredictionMessageDeliveryRecord | None = None,
        prediction_delivery: PredictionMessageDeliveryRecord | None = None,
    ) -> DailyPredictionProductionResult:
        return DailyPredictionProductionResult(
            outcome=outcome,
            generation_identity=generation_identity,
            input_identity=input_identity,
            delivery_identity=record.delivery_identity,
            timezone_name=window.timezone_name,
            today=window.today,
            tomorrow=window.tomorrow,
            generated_at=record.generated_at,
            error_summary=record.error_summary,
            result=result if result is not None else record.result,
            qualifying_delivery=qualifying_delivery,
            prediction_delivery=prediction_delivery,
        )
