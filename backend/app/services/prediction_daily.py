from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Protocol

from app.schemas.prediction import PredictionEvaluation
from app.schemas.prediction_booking import PredictionBookingResult
from app.schemas.prediction_daily import (
    DailyPredictionDeliveryOutcome,
    DailyPredictionDeliveryResult,
    PredictionGenerationDeliveryRecord,
    PredictionGenerationStatus,
)
from app.schemas.prediction import PredictionWindow
from app.schemas.sportybet_markets import OverOneHalfCandidate, SportyBetSelectionIdentity
from app.services.prediction_booking import PredictionBookingService
from app.services.prediction_evidence import (
    FootballEvidenceProvider,
    collect_football_evidence,
)
from app.services.prediction_evidence_engine import evaluate_evidence_predictions
from app.services.prediction_store import PredictionStore, prediction_store
from app.services.prediction_windows import today_tomorrow_window
from app.services.sportybet import get_upcoming_football_market_fixtures
from app.services.sportybet_markets import extract_over_one_half_candidates


logger = logging.getLogger('amen.prediction_daily')

PREDICTION_TIMEZONE_NAME = 'UTC'
PREDICTION_TIMEZONE = timezone.utc
DEFAULT_GENERATION_CLAIM_TTL_SECONDS = 300.0


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class DailyPredictionWindow:
    timezone_name: str
    today: date
    tomorrow: date
    window: PredictionWindow


def daily_prediction_window(now: datetime) -> DailyPredictionWindow:
    """Build the active UTC TODAY/TOMORROW window without server-local time."""

    current = _utc(now)
    window = today_tomorrow_window(current)
    today = current.date()
    return DailyPredictionWindow(
        timezone_name=PREDICTION_TIMEZONE_NAME,
        today=today,
        tomorrow=today + timedelta(days=1),
        window=window,
    )


def _identity_tuple(identity: SportyBetSelectionIdentity) -> tuple[str, str, str, int, str, str]:
    return (
        identity.event_id,
        identity.market_id,
        identity.outcome_id,
        identity.product_id,
        identity.sport_id,
        identity.specifier,
    )


def _hash_value(value: dict[str, Any]) -> str:
    serialized = json.dumps(
        value,
        separators=(',', ':'),
        ensure_ascii=True,
        sort_keys=True,
    )
    return hashlib.sha256(serialized.encode('utf-8')).hexdigest()


def prediction_generation_identity(window: DailyPredictionWindow) -> str:
    return _hash_value(
        {
            'timezone': window.timezone_name,
            'today': window.today.isoformat(),
            'tomorrow': window.tomorrow.isoformat(),
        }
    )


def _evaluation_input(evaluation: PredictionEvaluation) -> dict[str, Any]:
    explanation = evaluation.explanation
    return {
        'identity': _identity_tuple(evaluation.identity),
        'kickoff_at': _utc(evaluation.kickoff_at).isoformat(),
        'home_team': evaluation.home_team,
        'away_team': evaluation.away_team,
        'competition': evaluation.competition,
        'odds': evaluation.odds,
        'selected': evaluation.selected,
        'evidence_score': evaluation.evidence_score,
        'evidence_quality': (
            evaluation.evidence_quality.value
            if evaluation.evidence_quality is not None
            else None
        ),
        'model_version': evaluation.model_version,
        'positive_signals': (
            explanation.positive_signals if explanation is not None else []
        ),
        'negative_signals': (
            explanation.negative_signals if explanation is not None else []
        ),
        'missing_signals': (
            explanation.missing_signals if explanation is not None else []
        ),
    }


def prediction_input_identity(
    generation_identity: str,
    evaluations: list[PredictionEvaluation],
) -> str:
    unique: dict[tuple[str, str, str, int, str, str], PredictionEvaluation] = {}
    for evaluation in evaluations:
        unique.setdefault(_identity_tuple(evaluation.identity), evaluation)
    ordered = sorted(
        unique.values(),
        key=lambda evaluation: (
            _utc(evaluation.kickoff_at),
            evaluation.identity.event_id,
            _identity_tuple(evaluation.identity),
        ),
    )
    return _hash_value(
        {
            'generation_identity': generation_identity,
            'evaluations': [_evaluation_input(evaluation) for evaluation in ordered],
        }
    )


def _batch_identities(result: PredictionBookingResult) -> tuple[list[str], list[str], list[str]]:
    qualifying = [
        batch.batch_identity
        for day in (result.qualifying.today, result.qualifying.tomorrow)
        for batch in day.batches
    ]
    predictions = [
        batch.batch_identity
        for day in (result.predictions.today, result.predictions.tomorrow)
        for batch in day.batches
    ]
    booking_codes = [
        code
        for day in (
            result.qualifying.today,
            result.qualifying.tomorrow,
            result.predictions.today,
            result.predictions.tomorrow,
        )
        for code in day.booking_codes
    ]
    return qualifying, predictions, booking_codes


def prediction_delivery_identity(
    *,
    generation_identity: str,
    input_identity: str,
    result: PredictionBookingResult,
) -> tuple[str, list[str], list[str], list[str]]:
    qualifying, predictions, booking_codes = _batch_identities(result)
    identity = _hash_value(
        {
            'generation_identity': generation_identity,
            'input_identity': input_identity,
            'qualifying_batch_identities': qualifying,
            'prediction_batch_identities': predictions,
        }
    )
    return identity, qualifying, predictions, booking_codes


class PredictionEvaluationProvider(Protocol):
    async def evaluate(self, *, now: datetime) -> list[PredictionEvaluation]:
        ...


class SportyBetEvidenceEvaluationProvider:
    def __init__(
        self,
        evidence_provider: FootballEvidenceProvider,
        *,
        candidate_fetcher: Callable[..., Awaitable[Any]] | None = None,
        window_builder: Callable[[datetime], PredictionWindow] | None = None,
    ) -> None:
        self.evidence_provider = evidence_provider
        self.candidate_fetcher = candidate_fetcher or get_upcoming_football_market_fixtures
        self.window_builder = window_builder or today_tomorrow_window

    async def _candidates(self, *, now: datetime) -> list[OverOneHalfCandidate]:
        window = self.window_builder(now)
        catalogue = await self.candidate_fetcher(
            start_datetime=window.start,
            end_datetime=window.end_exclusive,
        )
        return extract_over_one_half_candidates(catalogue.fixtures)

    async def evaluate(self, *, now: datetime) -> list[PredictionEvaluation]:
        current = _utc(now)
        candidates = await self._candidates(now=current)
        evidence = collect_football_evidence(
            candidates,
            self.evidence_provider,
            now=current,
        )
        return evaluate_evidence_predictions(
            candidates,
            football_evidence_by_event_id=evidence.evidence_by_event_id,
            now=current,
        )


class PredictionResultTelegramTransport(Protocol):
    async def send_result(
        self,
        chat_id: int | str,
        result: PredictionBookingResult,
    ) -> PredictionBookingResult:
        ...


class DailyPredictionDeliveryService:
    def __init__(
        self,
        *,
        evaluation_provider: PredictionEvaluationProvider,
        booking_service: PredictionBookingService,
        telegram_delivery: PredictionResultTelegramTransport,
        store: PredictionStore | None = None,
        clock: Callable[[], datetime] | None = None,
        claim_ttl_seconds: float = DEFAULT_GENERATION_CLAIM_TTL_SECONDS,
    ) -> None:
        self.evaluation_provider = evaluation_provider
        self.booking_service = booking_service
        self.telegram_delivery = telegram_delivery
        self.store = store or prediction_store
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.claim_ttl_seconds = claim_ttl_seconds

    async def generate_daily_prediction_delivery(
        self,
        chat_id: int | str,
        *,
        now: datetime | None = None,
        page_size: int | None = None,
        max_pages: int | None = None,
    ) -> DailyPredictionDeliveryResult:
        current = _utc(now or self.clock())
        window = daily_prediction_window(current)
        generation_identity = prediction_generation_identity(window)
        input_identity = ''

        claim, claimed = self.store.claim_generation(
            generation_identity=generation_identity,
            timezone_name=window.timezone_name,
            today=window.today,
            tomorrow=window.tomorrow,
            now=current,
            ttl_seconds=self.claim_ttl_seconds,
        )
        if not claimed:
            return self._result(
                outcome=DailyPredictionDeliveryOutcome.in_progress,
                state=PredictionGenerationStatus.generating,
                window=window,
                generation_identity=generation_identity,
                input_identity='',
                record=claim,
            )

        if claim.result is not None and claim.input_identity is not None:
            return await self._deliver_record(claim, chat_id=chat_id, now=current)

        try:
            evaluations = await self.evaluation_provider.evaluate(now=current)
            input_identity = prediction_input_identity(generation_identity, evaluations)
            existing = self.store.find_generation_by_input(
                generation_identity=generation_identity,
                input_identity=input_identity,
            )
            if existing is not None and self._can_retry_record(existing):
                self.store.discard_generation_claim(claim.id)
                return await self._deliver_record(existing, chat_id=chat_id, now=current)
            if existing is not None and existing.status == PredictionGenerationStatus.delivered:
                self.store.discard_generation_claim(claim.id)
                return self._result(
                    outcome=DailyPredictionDeliveryOutcome.already_delivered,
                    state=PredictionGenerationStatus.delivered,
                    window=window,
                    generation_identity=generation_identity,
                    input_identity=input_identity,
                    record=existing,
                )

            booking_result = await self.booking_service.create_booking_pools(
                evaluations,
                now=current,
                page_size=page_size,
                max_pages=max_pages,
            )
            if booking_result.status.value == 'unavailable':
                failed = self.store.mark_generation_failed(
                    claim.id,
                    now=current,
                    error_summary='SportyBet catalogue unavailable',
                )
                return self._result(
                    outcome=DailyPredictionDeliveryOutcome.failed,
                    state=PredictionGenerationStatus.generation_failed,
                    window=window,
                    generation_identity=generation_identity,
                    input_identity=input_identity,
                    record=failed,
                    result=booking_result,
                )

            (
                delivery_identity,
                qualifying_batches,
                prediction_batches,
                booking_codes,
            ) = prediction_delivery_identity(
                generation_identity=generation_identity,
                input_identity=input_identity,
                result=booking_result,
            )
            existing_delivery = self.store.find_generation_by_delivery(delivery_identity)
            if existing_delivery is not None and self._can_retry_record(existing_delivery):
                self.store.discard_generation_claim(claim.id)
                return await self._deliver_record(
                    existing_delivery,
                    chat_id=chat_id,
                    now=current,
                )
            if (
                existing_delivery is not None
                and existing_delivery.status == PredictionGenerationStatus.delivered
            ):
                self.store.discard_generation_claim(claim.id)
                return self._result(
                    outcome=DailyPredictionDeliveryOutcome.already_delivered,
                    state=PredictionGenerationStatus.delivered,
                    window=window,
                    generation_identity=generation_identity,
                    input_identity=input_identity,
                    record=existing_delivery,
                )

            generated = self.store.mark_generation_generated(
                claim.id,
                input_identity=input_identity,
                delivery_identity=delivery_identity,
                generated_at=current,
                result=booking_result,
                qualifying_batch_identities=qualifying_batches,
                prediction_batch_identities=prediction_batches,
                booking_codes=booking_codes,
            )
        except Exception as exc:
            logger.warning(
                'Daily prediction generation failed: %s',
                type(exc).__name__,
            )
            failed = self.store.mark_generation_failed(
                claim.id,
                now=current,
                error_summary=f'Prediction generation failed: {type(exc).__name__}',
            )
            return self._result(
                outcome=DailyPredictionDeliveryOutcome.failed,
                state=PredictionGenerationStatus.generation_failed,
                window=window,
                generation_identity=generation_identity,
                input_identity=input_identity,
                record=failed,
            )

        return await self._deliver_record(
            generated,
            chat_id=chat_id,
            now=current,
        )

    async def _deliver_record(
        self,
        record: PredictionGenerationDeliveryRecord,
        *,
        chat_id: int | str,
        now: datetime,
    ) -> DailyPredictionDeliveryResult:
        if record.result is None:
            raise ValueError('Cannot deliver a prediction generation without a result')
        if record.status == PredictionGenerationStatus.delivered:
            return self._result(
                outcome=DailyPredictionDeliveryOutcome.already_delivered,
                state=PredictionGenerationStatus.delivered,
                window=DailyPredictionWindow(
                    timezone_name=record.timezone_name,
                    today=record.today,
                    tomorrow=record.tomorrow,
                    window=today_tomorrow_window(now),
                ),
                generation_identity=record.generation_identity,
                input_identity=record.input_identity or '',
                record=record,
            )

        pending = self.store.mark_generation_delivery_pending(record.id, now=now)
        try:
            await self.telegram_delivery.send_result(chat_id, pending.result)
        except Exception as exc:
            logger.warning('Daily Telegram delivery failed: %s', type(exc).__name__)
            failed = self.store.mark_generation_delivery_failed(
                record.id,
                now=now,
                error_summary=f'Telegram delivery failed: {type(exc).__name__}',
            )
            return self._result(
                outcome=DailyPredictionDeliveryOutcome.failed,
                state=PredictionGenerationStatus.delivery_failed,
                window=DailyPredictionWindow(
                    timezone_name=failed.timezone_name,
                    today=failed.today,
                    tomorrow=failed.tomorrow,
                    window=today_tomorrow_window(now),
                ),
                generation_identity=failed.generation_identity,
                input_identity=failed.input_identity or '',
                record=failed,
            )

        delivered = self.store.mark_generation_delivered(record.id, delivered_at=now)
        return self._result(
            outcome=DailyPredictionDeliveryOutcome.delivered,
            state=PredictionGenerationStatus.delivered,
            window=DailyPredictionWindow(
                timezone_name=delivered.timezone_name,
                today=delivered.today,
                tomorrow=delivered.tomorrow,
                window=today_tomorrow_window(now),
            ),
            generation_identity=delivered.generation_identity,
            input_identity=delivered.input_identity or '',
            record=delivered,
        )

    @staticmethod
    def _can_retry_record(record: PredictionGenerationDeliveryRecord) -> bool:
        return (
            record.result is not None
            and record.status
            in {
                PredictionGenerationStatus.generated,
                PredictionGenerationStatus.delivery_pending,
                PredictionGenerationStatus.delivery_failed,
            }
        )

    @staticmethod
    def _result(
        *,
        outcome: DailyPredictionDeliveryOutcome,
        state: PredictionGenerationStatus,
        window: DailyPredictionWindow,
        generation_identity: str,
        input_identity: str,
        record: PredictionGenerationDeliveryRecord,
        result: PredictionBookingResult | None = None,
    ) -> DailyPredictionDeliveryResult:
        return DailyPredictionDeliveryResult(
            outcome=outcome,
            state=state,
            generation_identity=generation_identity,
            input_identity=input_identity,
            delivery_identity=record.delivery_identity,
            timezone_name=window.timezone_name,
            today=window.today,
            tomorrow=window.tomorrow,
            generated_at=record.generated_at,
            delivered_at=record.delivered_at,
            error_summary=record.error_summary,
            result=result if result is not None else record.result,
        )
