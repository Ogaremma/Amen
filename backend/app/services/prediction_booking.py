from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any

from fastapi import HTTPException

from app.config.settings import get_settings
from app.schemas.prediction import EvidenceQuality, PredictionEvaluation
from app.schemas.prediction_booking import (
    PredictionBookingBatch,
    PredictionBookingBatchStatus,
    PredictionBookingCatalogueDiagnostics,
    PredictionBookingDay,
    PredictionBookingDayResult,
    PredictionBookingDayStatus,
    PredictionBookingGroup,
    PredictionBookingPool,
    PredictionBookingPoolResult,
    PredictionBookingRejection,
    PredictionBookingResult,
    PredictionBookingResultStatus,
    PredictionBookingSelection,
)
from app.services.prediction_grouping import split_prediction_selections
from app.schemas.prediction import PredictionWindow
from app.schemas.sportybet_markets import (
    OverOneHalfCandidate,
    SportyBetFixtureWithMarkets,
    SportyBetSelectionIdentity,
)
from app.services.prediction_evidence_engine import EVIDENCE_MODEL_VERSION
from app.services.prediction_store import PredictionStore, prediction_store
from app.services.prediction_windows import today_tomorrow_window
from app.services.sportybet import (
    _create_share_code,
    determine_game_status,
    get_upcoming_football_market_fixtures,
)
from app.services.sportybet_markets import (
    OVER_ONE_HALF_MARKET_ID,
    OVER_ONE_HALF_MAX_ODDS_EXCLUSIVE,
    OVER_ONE_HALF_MIN_ODDS,
    OVER_ONE_HALF_OUTCOME_ID,
    OVER_ONE_HALF_OUTCOME_LABEL,
    OVER_ONE_HALF_PRODUCT_ID,
    OVER_ONE_HALF_SPECIFIER,
    extract_over_one_half_candidates,
)

logger = logging.getLogger('amen.prediction_booking')

SPORTYBET_SELECTION_BATCH_SIZE = 50


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _identity_tuple(identity: SportyBetSelectionIdentity) -> tuple[str, str, str, int, str, str]:
    return (
        identity.event_id,
        identity.market_id,
        identity.outcome_id,
        identity.product_id,
        identity.sport_id,
        identity.specifier,
    )


def _selection_sort_key(selection: PredictionBookingSelection) -> tuple[datetime, str, tuple[str, str, str, int, str, str]]:
    return (
        _utc(selection.kickoff_at),
        selection.identity.event_id,
        _identity_tuple(selection.identity),
    )


def _candidate_sort_key(candidate: OverOneHalfCandidate) -> tuple[datetime, str, tuple[str, str, str, int, str, str]]:
    return (
        _utc(candidate.fixture.kickoff),
        candidate.identity.event_id,
        _identity_tuple(candidate.identity),
    )


def _deduplicate_candidates(
    candidates: list[OverOneHalfCandidate],
) -> list[OverOneHalfCandidate]:
    unique: dict[tuple[str, str, str, int, str, str], OverOneHalfCandidate] = {}
    for candidate in candidates:
        unique.setdefault(_identity_tuple(candidate.identity), candidate)
    return sorted(unique.values(), key=_candidate_sort_key)


def _deduplicate_evaluations(
    evaluations: list[PredictionEvaluation],
) -> list[PredictionEvaluation]:
    unique: dict[tuple[str, str, str, int, str, str], PredictionEvaluation] = {}
    for evaluation in evaluations:
        unique.setdefault(_identity_tuple(evaluation.identity), evaluation)
    return sorted(
        unique.values(),
        key=lambda evaluation: (
            _utc(evaluation.kickoff_at),
            evaluation.identity.event_id,
            _identity_tuple(evaluation.identity),
        ),
    )


def _day_for_kickoff(
    kickoff: datetime,
    now: datetime,
    *,
    day_timezone: tzinfo = timezone.utc,
) -> PredictionBookingDay:
    kickoff_local = _utc(kickoff).astimezone(day_timezone)
    current_local = _utc(now).astimezone(day_timezone)
    today_midnight = current_local.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow_midnight = today_midnight + timedelta(days=1)
    if kickoff_local < tomorrow_midnight:
        return PredictionBookingDay.today
    return PredictionBookingDay.tomorrow


def _catalogue_diagnostics(
    catalogue: Any,
    *,
    now: datetime,
) -> PredictionBookingCatalogueDiagnostics:
    settings = get_settings()
    fresh = catalogue.is_fresh(settings.sportybet_acquisition_ttl_seconds, now=now)
    return PredictionBookingCatalogueDiagnostics(
        expected_total=catalogue.total_num,
        retrieved_total=catalogue.retrieved_num
        if catalogue.retrieved_num is not None
        else len(catalogue.fixtures),
        parsed_fixtures=len(catalogue.fixtures),
        pages_fetched=catalogue.pages_fetched,
        pagination_complete=catalogue.complete,
        retrieved_at=catalogue.retrieved_at,
        fresh=fresh,
        authoritative=catalogue.complete and fresh,
    )


def validate_prediction_booking_candidate(
    candidate: OverOneHalfCandidate,
    *,
    now: datetime,
    window: PredictionWindow | None = None,
) -> str | None:
    fixture = candidate.fixture
    market = candidate.market
    outcome = candidate.outcome
    identity = candidate.identity
    settings = get_settings()

    if fixture.sport_id != settings.sportybet_football_sport_id:
        return 'event_is_not_football'
    if fixture.is_banned is True or market.is_banned is True:
        return 'market_or_fixture_is_banned'
    if identity.event_id != fixture.event_id or identity.sport_id != fixture.sport_id:
        return 'booking_identity_does_not_match_fixture'
    if identity.market_id != market.market_id or identity.specifier != (market.specifier or ''):
        return 'booking_identity_does_not_match_market'
    if identity.outcome_id != outcome.outcome_id or identity.product_id != market.product_id:
        return 'booking_identity_does_not_match_outcome'
    if market.market_id != OVER_ONE_HALF_MARKET_ID:
        return 'wrong_market_id'
    if market.specifier != OVER_ONE_HALF_SPECIFIER:
        return 'wrong_specifier'
    if market.product_id != OVER_ONE_HALF_PRODUCT_ID:
        return 'wrong_product_id'
    if outcome.outcome_id != OVER_ONE_HALF_OUTCOME_ID:
        return 'wrong_outcome_id'
    if (outcome.description or '').strip() != OVER_ONE_HALF_OUTCOME_LABEL:
        return 'wrong_outcome_label'
    if outcome.is_active is not True:
        return 'outcome_is_inactive'
    if outcome.odds is None or outcome.odds <= 0:
        return 'invalid_odds'
    if not OVER_ONE_HALF_MIN_ODDS <= outcome.odds < OVER_ONE_HALF_MAX_ODDS_EXCLUSIVE:
        return 'odds_outside_target_range'

    kickoff = _utc(fixture.kickoff)
    current = _utc(now)
    if kickoff <= current:
        return 'fixture_already_started'
    status = determine_game_status(fixture.match_status or fixture.status, kickoff, now=current)
    if status != 'upcoming':
        return 'fixture_is_not_pre_match'
    active_window = window or today_tomorrow_window(current)
    if kickoff < active_window.start or kickoff >= active_window.end_exclusive:
        return 'fixture_outside_today_tomorrow_window'
    return None


def _booking_payload(identity: SportyBetSelectionIdentity) -> dict[str, Any]:
    return {
        'eventId': identity.event_id,
        'marketId': identity.market_id,
        'outcomeId': identity.outcome_id,
        'productId': identity.product_id,
        'sportId': identity.sport_id,
        'specifier': identity.specifier,
    }


def _batch_identity(
    *,
    pool: PredictionBookingPool,
    day: PredictionBookingDay,
    selections: list[PredictionBookingSelection],
) -> str:
    value = {
        'pool': pool.value,
        'day': day.value,
        'identities': [_identity_tuple(selection.identity) for selection in selections],
    }
    return hashlib.sha256(
        json.dumps(value, separators=(',', ':'), ensure_ascii=True).encode('utf-8')
    ).hexdigest()


def _selection(
    candidate: OverOneHalfCandidate,
    *,
    evaluation: PredictionEvaluation | None = None,
) -> PredictionBookingSelection:
    return PredictionBookingSelection(
        identity=candidate.identity,
        home_team=candidate.fixture.home_team_name,
        away_team=candidate.fixture.away_team_name,
        competition=candidate.fixture.competition,
        kickoff_at=candidate.fixture.kickoff,
        odds=candidate.outcome.odds or 0.0,
        provider_probability=candidate.outcome.probability,
        match_status=candidate.fixture.match_status,
        baseline_score=evaluation.baseline_score if evaluation is not None else None,
        evidence_score=evaluation.evidence_score if evaluation is not None else None,
        evidence_quality=evaluation.evidence_quality if evaluation is not None else None,
        explanation=evaluation.explanation if evaluation is not None else None,
    )


def _current_market_rejection(
    evaluation: PredictionEvaluation,
    fixture: SportyBetFixtureWithMarkets | None,
    *,
    now: datetime,
    day_timezone: tzinfo = timezone.utc,
) -> PredictionBookingRejection:
    identity = evaluation.identity
    if fixture is None:
        return PredictionBookingRejection(
            pool=PredictionBookingPool.predictions,
            identity=identity,
            event_id=identity.event_id,
            reason='current_event_unavailable',
            predicted_odds=evaluation.odds,
        )

    current_odds: float | None = None
    settings = get_settings()
    if fixture.sport_id != settings.sportybet_football_sport_id:
        reason = 'current_event_is_not_football'
    elif _utc(fixture.kickoff) <= _utc(now):
        reason = 'current_fixture_already_started'
    else:
        window = today_tomorrow_window(_utc(now))
        kickoff = _utc(fixture.kickoff)
        if kickoff < window.start or kickoff >= window.end_exclusive:
            reason = 'current_fixture_outside_today_tomorrow_window'
        else:
            market = next(
                (
                    market
                    for market in fixture.markets
                    if market.market_id == identity.market_id
                ),
                None,
            )
            outcome = next(
                (
                    outcome
                    for outcome in (market.outcomes if market is not None else [])
                    if outcome.outcome_id == identity.outcome_id
                ),
                None,
            )
            current_odds = outcome.odds if outcome is not None else None
            if fixture.is_banned is True or (market is not None and market.is_banned is True):
                reason = 'current_market_or_fixture_banned'
            elif market is None:
                reason = 'current_market_unavailable'
            elif market.specifier != identity.specifier:
                reason = 'current_market_identity_changed'
            elif market.product_id != identity.product_id:
                reason = 'current_product_identity_changed'
            elif outcome is None:
                reason = 'current_outcome_unavailable'
            elif outcome.is_active is not True:
                reason = 'current_outcome_inactive'
            elif outcome.odds is None or outcome.odds <= 0:
                reason = 'current_odds_invalid'
            elif not OVER_ONE_HALF_MIN_ODDS <= outcome.odds < OVER_ONE_HALF_MAX_ODDS_EXCLUSIVE:
                reason = 'current_odds_outside_target_range'
            elif (outcome.description or '').strip() != OVER_ONE_HALF_OUTCOME_LABEL:
                reason = 'current_outcome_label_changed'
            else:
                reason = 'current_market_identity_changed'

    return PredictionBookingRejection(
        pool=PredictionBookingPool.predictions,
        day=_day_for_kickoff(
            evaluation.kickoff_at,
            now,
            day_timezone=day_timezone,
        ),
        identity=identity,
        event_id=identity.event_id,
        reason=reason,
        predicted_odds=evaluation.odds,
        current_odds=current_odds,
    )


def _build_selection_pools(
    current_candidates: list[OverOneHalfCandidate],
    current_fixtures: list[SportyBetFixtureWithMarkets],
    evaluations: list[PredictionEvaluation],
    *,
    now: datetime,
    day_timezone: tzinfo = timezone.utc,
    window: PredictionWindow | None = None,
) -> tuple[
    dict[PredictionBookingDay, list[PredictionBookingSelection]],
    dict[PredictionBookingDay, list[PredictionBookingRejection]],
    dict[PredictionBookingDay, list[PredictionBookingSelection]],
    dict[PredictionBookingDay, list[PredictionBookingRejection]],
]:
    candidates_by_identity = {
        _identity_tuple(candidate.identity): candidate
        for candidate in _deduplicate_candidates(current_candidates)
    }
    fixtures_by_event = {
        fixture.event_id: fixture
        for fixture in current_fixtures
    }
    qualifying: dict[PredictionBookingDay, list[PredictionBookingSelection]] = {
        day: [] for day in PredictionBookingDay
    }
    qualifying_rejections: dict[PredictionBookingDay, list[PredictionBookingRejection]] = {
        day: [] for day in PredictionBookingDay
    }
    predictions: dict[PredictionBookingDay, list[PredictionBookingSelection]] = {
        day: [] for day in PredictionBookingDay
    }
    prediction_rejections: dict[PredictionBookingDay, list[PredictionBookingRejection]] = {
        day: [] for day in PredictionBookingDay
    }

    for candidate in _deduplicate_candidates(current_candidates):
        reason = validate_prediction_booking_candidate(candidate, now=now, window=window)
        day = _day_for_kickoff(
            candidate.fixture.kickoff,
            now,
            day_timezone=day_timezone,
        )
        if reason is not None:
            qualifying_rejections[day].append(
                PredictionBookingRejection(
                    pool=PredictionBookingPool.qualifying,
                    day=day,
                    identity=candidate.identity,
                    event_id=candidate.identity.event_id,
                    reason=reason,
                    current_odds=candidate.outcome.odds,
                )
            )
            continue
        qualifying[day].append(_selection(candidate))

    for evaluation in _deduplicate_evaluations(evaluations):
        if not evaluation.selected:
            continue
        evaluation_day = _day_for_kickoff(
            evaluation.kickoff_at,
            now,
            day_timezone=day_timezone,
        )
        if evaluation.model_version != EVIDENCE_MODEL_VERSION:
            prediction_rejections[evaluation_day].append(
                PredictionBookingRejection(
                    pool=PredictionBookingPool.predictions,
                    day=evaluation_day,
                    identity=evaluation.identity,
                    event_id=evaluation.identity.event_id,
                    reason='prediction_not_evidence_v1',
                    predicted_odds=evaluation.odds,
                )
            )
            continue

        candidate = candidates_by_identity.get(_identity_tuple(evaluation.identity))
        if candidate is None:
            fixture = fixtures_by_event.get(evaluation.identity.event_id)
            rejection = _current_market_rejection(
                evaluation,
                fixture,
                now=now,
                day_timezone=day_timezone,
            )
            current_kickoff = fixture.kickoff if fixture is not None else evaluation.kickoff_at
            rejection = rejection.model_copy(
                update={
                    'day': _day_for_kickoff(
                        current_kickoff,
                        now,
                        day_timezone=day_timezone,
                    )
                }
            )
            prediction_rejections[rejection.day].append(rejection)
            continue

        reason = validate_prediction_booking_candidate(candidate, now=now, window=window)
        day = _day_for_kickoff(
            candidate.fixture.kickoff,
            now,
            day_timezone=day_timezone,
        )
        if reason is not None:
            prediction_rejections[day].append(
                PredictionBookingRejection(
                    pool=PredictionBookingPool.predictions,
                    day=day,
                    identity=candidate.identity,
                    event_id=candidate.identity.event_id,
                    reason=reason,
                    predicted_odds=evaluation.odds,
                    current_odds=candidate.outcome.odds,
                )
            )
            continue
        predictions[day].append(_selection(candidate, evaluation=evaluation))

    for day in PredictionBookingDay:
        qualifying[day].sort(key=_selection_sort_key)
        predictions[day].sort(key=_selection_sort_key)

    return qualifying, qualifying_rejections, predictions, prediction_rejections


def _error_message(exc: Exception) -> str:
    if isinstance(exc, HTTPException):
        return str(exc.detail)
    return f'{type(exc).__name__}: {exc}'


def _day_status(
    *,
    selections: list[PredictionBookingSelection],
    batches: list[PredictionBookingBatch],
    rejected: list[PredictionBookingRejection],
) -> PredictionBookingDayStatus:
    if not selections and not rejected:
        return PredictionBookingDayStatus.empty
    if not selections and rejected:
        return PredictionBookingDayStatus.rejected
    successful = [
        batch
        for batch in batches
        if batch.status in {PredictionBookingBatchStatus.created, PredictionBookingBatchStatus.reused}
        and batch.error is None
    ]
    failed = [batch for batch in batches if batch.status == PredictionBookingBatchStatus.failed or batch.error is not None]
    if failed and successful:
        return PredictionBookingDayStatus.partial
    if failed:
        return PredictionBookingDayStatus.failed
    if rejected:
        return PredictionBookingDayStatus.partial
    return PredictionBookingDayStatus.complete


class PredictionBookingService:
    def __init__(self, store: PredictionStore | None = prediction_store) -> None:
        self.store = store

    async def _book_batch(
        self,
        *,
        pool: PredictionBookingPool,
        day: PredictionBookingDay,
        batch_index: int,
        selections: list[PredictionBookingSelection],
        now: datetime,
    ) -> PredictionBookingBatch:
        batch_identity = _batch_identity(
            pool=pool,
            day=day,
            selections=selections,
        )

        if pool == PredictionBookingPool.predictions and self.store is not None:
            records = self.store.get_predictions_by_identities(
                [selection.identity for selection in selections]
            )
            reusable_codes = {
                record.booking_code
                for record in records
                if record.booking_code is not None
                and record.booking_pool == PredictionBookingPool.predictions.value
                and record.booking_batch_identity == batch_identity
            }
            if len(records) == len(selections) and len(reusable_codes) == 1:
                return PredictionBookingBatch(
                    pool=pool,
                    day=day,
                    batch_index=batch_index,
                    selection_count=len(selections),
                    selections=selections,
                    booking_code=next(iter(reusable_codes)),
                    batch_identity=batch_identity,
                    status=PredictionBookingBatchStatus.reused,
                )
            if any(record.booking_code is not None for record in records):
                return PredictionBookingBatch(
                    pool=pool,
                    day=day,
                    batch_index=batch_index,
                    selection_count=len(selections),
                    selections=selections,
                    batch_identity=batch_identity,
                    status=PredictionBookingBatchStatus.failed,
                    error='prediction_booking_idempotency_conflict',
                )

        try:
            booking_code = await _create_share_code(
                [_booking_payload(selection.identity) for selection in selections]
            )
        except Exception as exc:
            logger.exception(
                'prediction_booking_failed pool=%s day=%s batch_index=%s',
                pool.value,
                day.value,
                batch_index,
            )
            return PredictionBookingBatch(
                pool=pool,
                day=day,
                batch_index=batch_index,
                selection_count=len(selections),
                selections=selections,
                batch_identity=batch_identity,
                status=PredictionBookingBatchStatus.failed,
                error=_error_message(exc),
            )

        error: str | None = None
        if pool == PredictionBookingPool.predictions and self.store is not None:
            try:
                updated = self.store.attach_booking_metadata(
                    [selection.identity for selection in selections],
                    booking_pool=pool.value,
                    booking_code=booking_code,
                    booking_batch_identity=batch_identity,
                    booking_created_at=now,
                )
                if updated != len(selections):
                    error = 'prediction_booking_metadata_link_incomplete'
            except Exception as exc:
                logger.exception(
                    'prediction_booking_metadata_link_failed pool=%s day=%s batch_index=%s',
                    pool.value,
                    day.value,
                    batch_index,
                )
                error = _error_message(exc)

        return PredictionBookingBatch(
            pool=pool,
            day=day,
            batch_index=batch_index,
            selection_count=len(selections),
            selections=selections,
            booking_code=booking_code,
            batch_identity=batch_identity,
            status=PredictionBookingBatchStatus.created,
            error=error,
        )

    async def _book_day(
        self,
        *,
        pool: PredictionBookingPool,
        day: PredictionBookingDay,
        selections: list[PredictionBookingSelection],
        rejected: list[PredictionBookingRejection],
        now: datetime,
        selection_groups: list[list[PredictionBookingSelection]] | None = None,
    ) -> PredictionBookingDayResult:
        batches: list[PredictionBookingBatch] = []
        groups: list[PredictionBookingGroup] = []
        groups_to_book = (
            selection_groups
            if selection_groups is not None
            else ([selections] if selections else [])
        )
        batch_index = 1
        for group_index, group_selections in enumerate(groups_to_book, start=1):
            group_batches: list[PredictionBookingBatch] = []
            for offset in range(0, len(group_selections), SPORTYBET_SELECTION_BATCH_SIZE):
                batch_selections = group_selections[
                    offset:offset + SPORTYBET_SELECTION_BATCH_SIZE
                ]
                batch = await self._book_batch(
                    pool=pool,
                    day=day,
                    batch_index=batch_index,
                    selections=batch_selections,
                    now=now,
                )
                batches.append(batch)
                group_batches.append(batch)
                batch_index += 1
            if selection_groups is not None:
                groups.append(
                    PredictionBookingGroup(
                        group_index=group_index,
                        label=f'Group {group_index}',
                        selection_count=len(group_selections),
                        selections=group_selections,
                        batches=group_batches,
                        booking_codes=[
                            batch.booking_code
                            for batch in group_batches
                            if batch.booking_code is not None
                        ],
                        status=_day_status(
                            selections=group_selections,
                            batches=group_batches,
                            rejected=[],
                        ),
                    )
                )

        return PredictionBookingDayResult(
            pool=pool,
            day=day,
            selection_count=len(selections),
            selections=selections,
            batches=batches,
            booking_codes=[
                batch.booking_code
                for batch in batches
                if batch.booking_code is not None
            ],
            rejected=rejected,
            groups=groups,
            status=_day_status(
                selections=selections,
                batches=batches,
                rejected=rejected,
            ),
        )

    def _unavailable_day(
        self,
        *,
        pool: PredictionBookingPool,
        day: PredictionBookingDay,
        rejected: list[PredictionBookingRejection],
    ) -> PredictionBookingDayResult:
        return PredictionBookingDayResult(
            pool=pool,
            day=day,
            selection_count=0,
            selections=[],
            batches=[],
            booking_codes=[],
            rejected=rejected,
            status=PredictionBookingDayStatus.unavailable,
        )

    def _unavailable_result(
        self,
        *,
        now: datetime,
        window: PredictionWindow,
        diagnostics: PredictionBookingCatalogueDiagnostics,
        evaluations: list[PredictionEvaluation],
    ) -> PredictionBookingResult:
        rejections: dict[PredictionBookingDay, list[PredictionBookingRejection]] = {
            day: [] for day in PredictionBookingDay
        }
        for evaluation in _deduplicate_evaluations(evaluations):
            if not evaluation.selected:
                continue
            day = _day_for_kickoff(evaluation.kickoff_at, now)
            rejections[day].append(
                PredictionBookingRejection(
                    pool=PredictionBookingPool.predictions,
                    day=day,
                    identity=evaluation.identity,
                    event_id=evaluation.identity.event_id,
                    reason='current_catalogue_unavailable',
                    predicted_odds=evaluation.odds,
                )
            )

        return PredictionBookingResult(
            generated_at=now,
            window_start=window.start,
            window_end_exclusive=window.end_exclusive,
            catalogue=diagnostics,
            qualifying=PredictionBookingPoolResult(
                pool=PredictionBookingPool.qualifying,
                today=self._unavailable_day(
                    pool=PredictionBookingPool.qualifying,
                    day=PredictionBookingDay.today,
                    rejected=[],
                ),
                tomorrow=self._unavailable_day(
                    pool=PredictionBookingPool.qualifying,
                    day=PredictionBookingDay.tomorrow,
                    rejected=[],
                ),
            ),
            predictions=PredictionBookingPoolResult(
                pool=PredictionBookingPool.predictions,
                today=self._unavailable_day(
                    pool=PredictionBookingPool.predictions,
                    day=PredictionBookingDay.today,
                    rejected=rejections[PredictionBookingDay.today],
                ),
                tomorrow=self._unavailable_day(
                    pool=PredictionBookingPool.predictions,
                    day=PredictionBookingDay.tomorrow,
                    rejected=rejections[PredictionBookingDay.tomorrow],
                ),
            ),
            status=PredictionBookingResultStatus.unavailable,
        )

    async def create_booking_pools(
        self,
        evaluations: list[PredictionEvaluation],
        *,
        now: datetime | None = None,
        page_size: int | None = None,
        max_pages: int | None = None,
        window: PredictionWindow | None = None,
        day_timezone: tzinfo = timezone.utc,
        prediction_groups: bool = False,
    ) -> PredictionBookingResult:
        current = _utc(now or datetime.now(timezone.utc))
        active_window = window or today_tomorrow_window(current)
        selected_evaluations = [
            evaluation
            for evaluation in evaluations
            if evaluation.selected
            and evaluation.model_version == EVIDENCE_MODEL_VERSION
        ]

        persistence_error: str | None = None
        if self.store is not None and selected_evaluations:
            try:
                self.store.save_predictions(selected_evaluations)
            except Exception as exc:
                persistence_error = _error_message(exc)
                logger.exception('prediction_booking_persistence_failed')

        try:
            catalogue = await get_upcoming_football_market_fixtures(
                start_datetime=active_window.start,
                end_datetime=active_window.end_exclusive,
                page_size=page_size,
                max_pages=max_pages,
            )
            diagnostics = _catalogue_diagnostics(catalogue, now=current)
        except Exception as exc:
            logger.exception('prediction_booking_catalogue_failed')
            diagnostics = PredictionBookingCatalogueDiagnostics(
                expected_total=0,
                retrieved_total=0,
                parsed_fixtures=0,
                pages_fetched=0,
                pagination_complete=False,
                retrieved_at=None,
                fresh=False,
                authoritative=False,
                error=_error_message(exc),
            )
            return self._unavailable_result(
                now=current,
                window=active_window,
                diagnostics=diagnostics,
                evaluations=selected_evaluations,
            )

        if not diagnostics.authoritative:
            return self._unavailable_result(
                now=current,
                window=active_window,
                diagnostics=diagnostics,
                evaluations=selected_evaluations,
            )

        current_candidates = extract_over_one_half_candidates(catalogue.fixtures)
        (
            qualifying,
            qualifying_rejections,
            predictions,
            prediction_rejections,
        ) = _build_selection_pools(
            current_candidates,
            catalogue.fixtures,
            evaluations,
            now=current,
            day_timezone=day_timezone,
            window=active_window,
        )

        if persistence_error is not None:
            for day in PredictionBookingDay:
                for selection in predictions[day]:
                    prediction_rejections[day].append(
                        PredictionBookingRejection(
                            pool=PredictionBookingPool.predictions,
                            day=day,
                            identity=selection.identity,
                            event_id=selection.identity.event_id,
                            reason='prediction_persistence_failed',
                            predicted_odds=selection.odds,
                        )
                    )
                predictions[day] = []

        qualifying_today = await self._book_day(
            pool=PredictionBookingPool.qualifying,
            day=PredictionBookingDay.today,
            selections=qualifying[PredictionBookingDay.today],
            rejected=qualifying_rejections[PredictionBookingDay.today],
            now=current,
        )
        qualifying_tomorrow = await self._book_day(
            pool=PredictionBookingPool.qualifying,
            day=PredictionBookingDay.tomorrow,
            selections=qualifying[PredictionBookingDay.tomorrow],
            rejected=qualifying_rejections[PredictionBookingDay.tomorrow],
            now=current,
        )
        prediction_today = await self._book_day(
            pool=PredictionBookingPool.predictions,
            day=PredictionBookingDay.today,
            selections=predictions[PredictionBookingDay.today],
            rejected=prediction_rejections[PredictionBookingDay.today],
            now=current,
            selection_groups=(
                split_prediction_selections(predictions[PredictionBookingDay.today])
                if prediction_groups
                else None
            ),
        )
        prediction_tomorrow = await self._book_day(
            pool=PredictionBookingPool.predictions,
            day=PredictionBookingDay.tomorrow,
            selections=predictions[PredictionBookingDay.tomorrow],
            rejected=prediction_rejections[PredictionBookingDay.tomorrow],
            now=current,
            selection_groups=(
                split_prediction_selections(predictions[PredictionBookingDay.tomorrow])
                if prediction_groups
                else None
            ),
        )

        day_results = [
            qualifying_today,
            qualifying_tomorrow,
            prediction_today,
            prediction_tomorrow,
        ]
        status = (
            PredictionBookingResultStatus.partial
            if any(
                result.status
                not in {PredictionBookingDayStatus.complete, PredictionBookingDayStatus.empty}
                for result in day_results
            )
            else PredictionBookingResultStatus.complete
        )

        return PredictionBookingResult(
            generated_at=current,
            window_start=active_window.start,
            window_end_exclusive=active_window.end_exclusive,
            catalogue=diagnostics,
            qualifying=PredictionBookingPoolResult(
                pool=PredictionBookingPool.qualifying,
                today=qualifying_today,
                tomorrow=qualifying_tomorrow,
            ),
            predictions=PredictionBookingPoolResult(
                pool=PredictionBookingPool.predictions,
                today=prediction_today,
                tomorrow=prediction_tomorrow,
            ),
            status=status,
        )


prediction_booking_service = PredictionBookingService()
