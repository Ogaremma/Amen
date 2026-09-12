from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

from app.schemas.booking import BookingResponse, BookingSelection
from app.schemas.prediction import PredictionRecord, PredictionStatus
from app.schemas.prediction_settlement import (
    PredictionMatchStatus,
    PredictionSettlementApplyOutcome,
    PredictionSettlementDiagnostic,
    PredictionSettlementOutcome,
    PredictionSettlementSummary,
    PredictionSettlementUpdate,
)
from app.services.prediction_store import PredictionStore, prediction_store
from app.services.sportybet import get_booking


BookingProvider = Callable[[str], Awaitable[BookingResponse]]

_FINAL_MATCH_STATUSES = {
    'ended',
    'finished',
    'completed',
    'complete',
    'closed',
}
_LIVE_MATCH_STATUSES = {
    'live',
    'in progress',
    'in play',
    'started',
    'playing',
}
_UPCOMING_MATCH_STATUSES = {
    'not start',
    'not started',
    'scheduled',
    'upcoming',
    'pre match',
    'prematch',
}
_POSTPONED_MATCH_STATUSES = {'postponed', 'post pone'}
_CANCELLED_MATCH_STATUSES = {'cancelled', 'canceled'}
_ABANDONED_MATCH_STATUSES = {'abandoned', 'abandon'}
_DEFAULT_PRE_KICKOFF_LEAD_TIME = timedelta(minutes=10)


def _utc_now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def normalize_match_status(raw_status: str | None) -> PredictionMatchStatus:
    normalized = ' '.join(
        str(raw_status or '').strip().lower().replace('_', ' ').split()
    )
    if normalized in _FINAL_MATCH_STATUSES:
        return PredictionMatchStatus.final
    if normalized in _LIVE_MATCH_STATUSES:
        return PredictionMatchStatus.live
    if normalized in _UPCOMING_MATCH_STATUSES:
        return PredictionMatchStatus.upcoming
    if normalized in _POSTPONED_MATCH_STATUSES:
        return PredictionMatchStatus.postponed
    if normalized in _CANCELLED_MATCH_STATUSES:
        return PredictionMatchStatus.cancelled
    if normalized in _ABANDONED_MATCH_STATUSES:
        return PredictionMatchStatus.abandoned
    return PredictionMatchStatus.unknown


def _selection_identity(selection: BookingSelection) -> tuple[str, str, str, int | None, str | None, str | None]:
    return (
        selection.event_id,
        selection.market_id,
        selection.outcome_id,
        selection.product_id,
        selection.sport_id,
        selection.specifier,
    )


def _prediction_identity(record: PredictionRecord) -> tuple[str, str, str, int, str, str]:
    return (
        record.event_id,
        record.market_id,
        record.outcome_id,
        record.product_id,
        record.sport_id,
        record.specifier,
    )


def _is_due(record: PredictionRecord, now: datetime, lead_time: timedelta) -> bool:
    if record.prediction_status == PredictionStatus.live:
        return True
    kickoff = record.kickoff_at
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=timezone.utc)
    return kickoff <= now + lead_time


def _settlement_update(
    record: PredictionRecord,
    selection: BookingSelection,
    now: datetime,
) -> PredictionSettlementUpdate:
    match_status = normalize_match_status(selection.status)
    live_status = selection.status

    if match_status == PredictionMatchStatus.postponed:
        return PredictionSettlementUpdate(
            prediction_id=record.id,
            event_id=record.event_id,
            prediction_status=PredictionStatus.postponed,
            live_status=live_status,
            reason='postponed',
        )
    if match_status == PredictionMatchStatus.cancelled:
        return PredictionSettlementUpdate(
            prediction_id=record.id,
            event_id=record.event_id,
            prediction_status=PredictionStatus.cancelled,
            live_status=live_status,
            reason='cancelled',
        )
    if match_status == PredictionMatchStatus.abandoned:
        return PredictionSettlementUpdate(
            prediction_id=record.id,
            event_id=record.event_id,
            prediction_status=PredictionStatus.abandoned,
            live_status=live_status,
            reason='abandoned',
        )
    if match_status == PredictionMatchStatus.live:
        return PredictionSettlementUpdate(
            prediction_id=record.id,
            event_id=record.event_id,
            prediction_status=PredictionStatus.live,
            live_status=live_status,
            current_home_goals=selection.home_score,
            current_away_goals=selection.away_score,
            reason=(
                None
                if selection.home_score is not None and selection.away_score is not None
                else 'invalid_or_missing_live_score'
            ),
        )
    if match_status == PredictionMatchStatus.upcoming:
        return PredictionSettlementUpdate(
            prediction_id=record.id,
            event_id=record.event_id,
            prediction_status=PredictionStatus.pending,
            live_status=live_status,
            reason='not_started',
        )
    if match_status != PredictionMatchStatus.final:
        return PredictionSettlementUpdate(
            prediction_id=record.id,
            event_id=record.event_id,
            prediction_status=PredictionStatus.unresolved,
            live_status=live_status,
            reason='unknown_match_status',
        )
    if selection.home_score is None or selection.away_score is None:
        return PredictionSettlementUpdate(
            prediction_id=record.id,
            event_id=record.event_id,
            prediction_status=PredictionStatus.unresolved,
            live_status=live_status,
            reason='invalid_or_missing_final_score',
        )

    actual_goals = selection.home_score + selection.away_score
    won = actual_goals >= 2
    return PredictionSettlementUpdate(
        prediction_id=record.id,
        event_id=record.event_id,
        prediction_status=(
            PredictionStatus.settled_win if won else PredictionStatus.settled_miss
        ),
        live_status=live_status,
        current_home_goals=selection.home_score,
        current_away_goals=selection.away_score,
        actual_goals=actual_goals,
        actual_result='win' if won else 'miss',
        settled_at=now,
        reason='final_total_goals',
    )


class PredictionSettlementService:
    def __init__(
        self,
        store: PredictionStore | None = None,
        *,
        booking_provider: BookingProvider = get_booking,
        pre_kickoff_lead_time: timedelta = _DEFAULT_PRE_KICKOFF_LEAD_TIME,
    ) -> None:
        self.store = store or prediction_store
        self.booking_provider = booking_provider
        self.pre_kickoff_lead_time = pre_kickoff_lead_time

    async def monitor_and_settle_predictions(
        self,
        *,
        now: datetime | None = None,
    ) -> PredictionSettlementSummary:
        current = _utc_now(now)
        candidates = self.store.list_settlement_candidates()
        summary = PredictionSettlementSummary(generated_at=current)
        due_by_booking_code: dict[str, list[PredictionRecord]] = defaultdict(list)

        for record in candidates:
            if not _is_due(record, current, self.pre_kickoff_lead_time):
                self._add_result(
                    summary,
                    record,
                    PredictionSettlementOutcome.not_due,
                    record.prediction_status,
                )
                continue
            if not record.booking_code:
                update = PredictionSettlementUpdate(
                    prediction_id=record.id,
                    event_id=record.event_id,
                    prediction_status=PredictionStatus.unresolved,
                    reason='missing_booking_code',
                )
                self._apply_update(summary, record, update, record.booking_code)
                continue
            due_by_booking_code[record.booking_code].append(record)

        for booking_code, records in due_by_booking_code.items():
            summary.provider_calls += 1
            try:
                booking = await self.booking_provider(booking_code)
            except Exception:
                for record in records:
                    self._add_result(
                        summary,
                        record,
                        PredictionSettlementOutcome.provider_failure,
                        record.prediction_status,
                        booking_code=booking_code,
                        reason='booking_provider_unavailable',
                    )
                    summary.provider_failures += 1
                continue

            if not isinstance(booking, BookingResponse):
                for record in records:
                    self._add_result(
                        summary,
                        record,
                        PredictionSettlementOutcome.provider_failure,
                        record.prediction_status,
                        booking_code=booking_code,
                        reason='invalid_booking_response',
                    )
                    summary.provider_failures += 1
                continue

            selections_by_identity: dict[
                tuple[str, str, str, int | None, str | None, str | None],
                list[BookingSelection],
            ] = defaultdict(list)
            for selection in booking.selections:
                selections_by_identity[_selection_identity(selection)].append(selection)

            for record in records:
                exact = selections_by_identity.get(_prediction_identity(record))
                if exact is None:
                    reason = (
                        'identity_mismatch'
                        if any(selection.event_id == record.event_id for selection in booking.selections)
                        else 'exact_identity_not_found'
                    )
                    self._apply_update(
                        summary,
                        record,
                        PredictionSettlementUpdate(
                            prediction_id=record.id,
                            event_id=record.event_id,
                            prediction_status=PredictionStatus.unresolved,
                            reason=reason,
                        ),
                        booking_code,
                    )
                    continue
                if len(exact) != 1:
                    self._apply_update(
                        summary,
                        record,
                        PredictionSettlementUpdate(
                            prediction_id=record.id,
                            event_id=record.event_id,
                            prediction_status=PredictionStatus.unresolved,
                            reason='ambiguous_selection_identity',
                        ),
                        booking_code,
                    )
                    continue
                self._apply_update(
                    summary,
                    record,
                    _settlement_update(record, exact[0], current),
                    booking_code,
                )

        return summary

    def _apply_update(
        self,
        summary: PredictionSettlementSummary,
        record: PredictionRecord,
        update: PredictionSettlementUpdate,
        booking_code: str | None,
    ) -> None:
        apply_outcome, updated_record = self.store.apply_settlement_update(update)
        if apply_outcome == PredictionSettlementApplyOutcome.updated:
            outcome = _status_outcome(update.prediction_status)
            status = update.prediction_status
            home_score = update.current_home_goals
            away_score = update.current_away_goals
            actual_goals = update.actual_goals
            reason = update.reason
        elif apply_outcome == PredictionSettlementApplyOutcome.skipped_already_settled:
            outcome = PredictionSettlementOutcome.skipped_already_settled
            status = updated_record.prediction_status if updated_record else record.prediction_status
            home_score = updated_record.current_home_goals if updated_record else None
            away_score = updated_record.current_away_goals if updated_record else None
            actual_goals = updated_record.actual_goals if updated_record else None
            reason = 'already_settled'
        else:
            outcome = PredictionSettlementOutcome.conflict
            status = updated_record.prediction_status if updated_record else record.prediction_status
            home_score = updated_record.current_home_goals if updated_record else None
            away_score = updated_record.current_away_goals if updated_record else None
            actual_goals = updated_record.actual_goals if updated_record else None
            reason = 'settlement_conflict'
        self._add_result(
            summary,
            record,
            outcome,
            status,
            booking_code=booking_code,
            home_score=home_score,
            away_score=away_score,
            actual_goals=actual_goals,
            reason=reason,
        )

    @staticmethod
    def _add_result(
        summary: PredictionSettlementSummary,
        record: PredictionRecord,
        outcome: PredictionSettlementOutcome,
        status: PredictionStatus,
        *,
        booking_code: str | None = None,
        home_score: int | None = None,
        away_score: int | None = None,
        actual_goals: int | None = None,
        reason: str | None = None,
    ) -> None:
        summary.diagnostics.append(
            PredictionSettlementDiagnostic(
                prediction_id=record.id,
                event_id=record.event_id,
                booking_code=booking_code,
                outcome=outcome,
                prediction_status=status,
                home_score=home_score,
                away_score=away_score,
                actual_goals=actual_goals,
                reason=reason,
            )
        )
        if outcome == PredictionSettlementOutcome.not_due:
            summary.not_due += 1
        elif outcome == PredictionSettlementOutcome.live:
            summary.live += 1
        elif outcome == PredictionSettlementOutcome.settled_win:
            summary.settled_win += 1
        elif outcome == PredictionSettlementOutcome.settled_miss:
            summary.settled_miss += 1
        elif outcome == PredictionSettlementOutcome.postponed:
            summary.postponed += 1
        elif outcome == PredictionSettlementOutcome.cancelled:
            summary.cancelled += 1
        elif outcome == PredictionSettlementOutcome.abandoned:
            summary.abandoned += 1
        elif outcome == PredictionSettlementOutcome.unresolved:
            summary.unresolved += 1
        elif outcome == PredictionSettlementOutcome.skipped_already_settled:
            summary.skipped_already_settled += 1


def _status_outcome(status: PredictionStatus) -> PredictionSettlementOutcome:
    mapping = {
        PredictionStatus.pending: PredictionSettlementOutcome.unresolved,
        PredictionStatus.live: PredictionSettlementOutcome.live,
        PredictionStatus.settled_win: PredictionSettlementOutcome.settled_win,
        PredictionStatus.settled_miss: PredictionSettlementOutcome.settled_miss,
        PredictionStatus.postponed: PredictionSettlementOutcome.postponed,
        PredictionStatus.cancelled: PredictionSettlementOutcome.cancelled,
        PredictionStatus.abandoned: PredictionSettlementOutcome.abandoned,
        PredictionStatus.unresolved: PredictionSettlementOutcome.unresolved,
    }
    return mapping.get(status, PredictionSettlementOutcome.unresolved)


prediction_settlement_service = PredictionSettlementService()
