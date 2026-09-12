from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import (
    BigInteger,
    JSON,
    Column,
    Date,
    DateTime,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    and_,
    create_engine,
    delete,
    desc,
    inspect,
    or_,
    select,
    update,
)
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

from app.config.settings import get_settings
from app.schemas.prediction_daily import PredictionGenerationDeliveryRecord
from app.schemas.prediction_daily_production import (
    PredictionMessageCategory,
    PredictionMessageDeliveryRecord,
    PredictionMessageDeliveryStatus,
)
from app.schemas.prediction import (
    EvidenceQuality,
    PredictionEvaluation,
    PredictionExplanation,
    PredictionFeatureSnapshot,
    PredictionRecord,
    PredictionSaveResult,
    PredictionStatus,
)
from app.schemas.prediction_settlement import (
    PredictionSettlementApplyOutcome,
    PredictionSettlementUpdate,
)
from app.schemas.sportybet_markets import SportyBetSelectionIdentity
from app.services.history_store import _database_url

metadata = MetaData()

prediction_records = Table(
    'prediction_records',
    metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('event_id', String(128), nullable=False),
    Column('sport_id', String(64), nullable=False),
    Column('market_id', String(64), nullable=False),
    Column('outcome_id', String(64), nullable=False),
    Column('product_id', Integer, nullable=False),
    Column('specifier', String(256), nullable=False),
    Column('home_team', String(256), nullable=False),
    Column('away_team', String(256), nullable=False),
    Column('competition', String(256), nullable=True),
    Column('kickoff_at', DateTime(timezone=True), nullable=False),
    Column('prediction_market', String(64), nullable=False),
    Column('odds_at_prediction', Float, nullable=False),
    Column('baseline_score', Float, nullable=True),
    Column('evidence_score', Float, nullable=True),
    Column('evidence_quality', String(32), nullable=True),
    Column('model_score', Float, nullable=False),
    Column('confidence', Float, nullable=False),
    Column('selection_reason', Text, nullable=False),
    Column('provider_probability', Float, nullable=True),
    Column('provider_probability_source', Float, nullable=True),
    Column('explanation', JSON, nullable=True),
    Column('booking_pool', String(32), nullable=True),
    Column('booking_code', String(64), nullable=True),
    Column('booking_batch_identity', String(128), nullable=True),
    Column('booking_created_at', DateTime(timezone=True), nullable=True),
    Column('feature_snapshot', JSON, nullable=False),
    Column('prediction_status', String(32), nullable=False, default='pending'),
    Column('live_status', String(64), nullable=True),
    Column('current_home_goals', Integer, nullable=True),
    Column('current_away_goals', Integer, nullable=True),
    Column('last_score_update_at', DateTime(timezone=True), nullable=True),
    Column('actual_goals', Integer, nullable=True),
    Column('actual_result', String(32), nullable=True),
    Column('settled_at', DateTime(timezone=True), nullable=True),
    Column('model_version', String(64), nullable=False),
    Column('created_at', DateTime(timezone=True), nullable=False),
    Column('updated_at', DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        'event_id',
        'market_id',
        'outcome_id',
        'product_id',
        'sport_id',
        'specifier',
        name='uq_prediction_selection_identity',
    ),
)

prediction_generation_deliveries = Table(
    'prediction_generation_deliveries',
    metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('claim_identity', String(128), nullable=True, unique=True),
    Column('generation_identity', String(128), nullable=False, index=True),
    Column('input_identity', String(128), nullable=True, index=True),
    Column('delivery_identity', String(128), nullable=True, unique=True),
    Column('timezone_name', String(64), nullable=False),
    Column('today', Date, nullable=False),
    Column('tomorrow', Date, nullable=False),
    Column('generated_at', DateTime(timezone=True), nullable=True),
    Column('status', String(32), nullable=False),
    Column('delivered_at', DateTime(timezone=True), nullable=True),
    Column('error_summary', Text, nullable=True),
    Column('result_snapshot', JSON, nullable=True),
    Column('qualifying_batch_identities', JSON, nullable=True),
    Column('prediction_batch_identities', JSON, nullable=True),
    Column('booking_codes', JSON, nullable=True),
    Column('claim_expires_at', DateTime(timezone=True), nullable=True),
    Column('created_at', DateTime(timezone=True), nullable=False),
    Column('updated_at', DateTime(timezone=True), nullable=False),
)

prediction_message_deliveries = Table(
    'prediction_message_deliveries',
    metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('generation_identity', String(128), nullable=False),
    Column('category', String(32), nullable=False),
    Column('delivery_identity', String(128), nullable=False),
    Column('input_identity', String(128), nullable=False),
    Column('telegram_user_id', BigInteger, nullable=True),
    Column('batch_identities', JSON, nullable=False),
    Column('booking_codes', JSON, nullable=False),
    Column('payload', JSON, nullable=False),
    Column('status', String(32), nullable=False, default='pending'),
    Column('sent_at', DateTime(timezone=True), nullable=True),
    Column('error_summary', Text, nullable=True),
    Column('retry_count', Integer, nullable=False, default=0),
    Column('claim_expires_at', DateTime(timezone=True), nullable=True),
    Column('created_at', DateTime(timezone=True), nullable=False),
    Column('updated_at', DateTime(timezone=True), nullable=False),
    UniqueConstraint('delivery_identity', name='uq_prediction_message_delivery_identity'),
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


def _identity_condition(identity: SportyBetSelectionIdentity):
    return and_(
        prediction_records.c.event_id == identity.event_id,
        prediction_records.c.market_id == identity.market_id,
        prediction_records.c.outcome_id == identity.outcome_id,
        prediction_records.c.product_id == identity.product_id,
        prediction_records.c.sport_id == identity.sport_id,
        prediction_records.c.specifier == identity.specifier,
    )


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class PredictionStore:
    def __init__(self, path: str | None = None, *, database_url: str | None = None) -> None:
        settings = get_settings()
        configured_url = database_url if database_url is not None else (None if path else settings.database_url)
        url = _database_url(path, configured_url)
        options = {'pool_pre_ping': True}
        if url.startswith('sqlite:'):
            options['poolclass'] = NullPool
        self.engine: Engine = create_engine(url, **options)
        self._initialized = False

    def _ensure_schema(self) -> None:
        if not self._initialized:
            metadata.create_all(self.engine)
            self._add_missing_prediction_record_columns()
            self._add_missing_prediction_delivery_columns()
            self._initialized = True

    def _add_missing_prediction_delivery_columns(self) -> None:
        inspector = inspect(self.engine)
        existing = {
            column['name']
            for column in inspector.get_columns('prediction_message_deliveries')
        }
        additions = {
            'telegram_user_id': BigInteger,
        }
        missing = [
            (name, column_type)
            for name, column_type in additions.items()
            if name not in existing
        ]
        if not missing:
            return
        with self.engine.begin() as db:
            for name, column_type in missing:
                db.execute(text(
                    'ALTER TABLE prediction_message_deliveries '
                    f'ADD COLUMN {name} {column_type.compile(self.engine.dialect)}'
                ))

    def _add_missing_prediction_record_columns(self) -> None:
        inspector = inspect(self.engine)
        existing = {
            column['name']
            for column in inspector.get_columns('prediction_records')
        }
        additions = {
            'live_status': String(64),
            'current_home_goals': Integer,
            'current_away_goals': Integer,
            'last_score_update_at': DateTime(timezone=True),
        }
        missing = [
            (name, column_type)
            for name, column_type in additions.items()
            if name not in existing
        ]
        if not missing:
            return
        with self.engine.begin() as db:
            for name, column_type in missing:
                db.execute(text(
                    'ALTER TABLE prediction_records '
                    f'ADD COLUMN {name} {column_type.compile(self.engine.dialect)}'
                ))

    def save_predictions(self, evaluations: list[PredictionEvaluation]) -> PredictionSaveResult:
        self._ensure_schema()
        selected = [evaluation for evaluation in evaluations if evaluation.selected]
        ignored_not_selected = len(evaluations) - len(selected)

        unique: dict[tuple[str, str, str, int, str, str], PredictionEvaluation] = {}
        for evaluation in selected:
            unique.setdefault(_identity_tuple(evaluation.identity), evaluation)

        if not unique:
            return PredictionSaveResult(inserted=0, skipped_existing=0, ignored_not_selected=ignored_not_selected)

        conditions = [
            _identity_condition(evaluation.identity)
            for evaluation in unique.values()
        ]
        with self.engine.begin() as db:
            existing_rows = db.execute(
                select(
                    prediction_records.c.event_id,
                    prediction_records.c.market_id,
                    prediction_records.c.outcome_id,
                    prediction_records.c.product_id,
                    prediction_records.c.sport_id,
                    prediction_records.c.specifier,
                ).where(or_(*conditions))
            ).all()
            existing = {tuple(row) for row in existing_rows}

            now = datetime.now(timezone.utc)
            inserted = 0
            for identity_tuple, evaluation in unique.items():
                if identity_tuple in existing:
                    continue
                db.execute(
                    prediction_records.insert().values(
                        event_id=evaluation.identity.event_id,
                        sport_id=evaluation.identity.sport_id,
                        market_id=evaluation.identity.market_id,
                        outcome_id=evaluation.identity.outcome_id,
                        product_id=evaluation.identity.product_id,
                        specifier=evaluation.identity.specifier,
                        home_team=evaluation.home_team,
                        away_team=evaluation.away_team,
                        competition=evaluation.competition,
                        kickoff_at=evaluation.kickoff_at,
                        prediction_market=evaluation.prediction_market,
                        odds_at_prediction=evaluation.odds,
                        baseline_score=evaluation.baseline_score,
                        evidence_score=evaluation.evidence_score,
                        evidence_quality=(
                            evaluation.evidence_quality.value
                            if evaluation.evidence_quality is not None
                            else None
                        ),
                        model_score=evaluation.model_score,
                        confidence=evaluation.confidence,
                        selection_reason=evaluation.selection_reason,
                        provider_probability=evaluation.provider_probability,
                        provider_probability_source=evaluation.provider_probability_source,
                        explanation=(
                            evaluation.explanation.model_dump(mode='json')
                            if evaluation.explanation is not None
                            else None
                        ),
                        feature_snapshot=evaluation.feature_snapshot.model_dump(mode='json'),
                        prediction_status=PredictionStatus.pending.value,
                        model_version=evaluation.model_version,
                        created_at=now,
                        updated_at=now,
                    )
                )
                inserted += 1

        return PredictionSaveResult(
            inserted=inserted,
            skipped_existing=len(unique) - inserted,
            ignored_not_selected=ignored_not_selected,
        )

    def list_predictions(self) -> list[PredictionRecord]:
        self._ensure_schema()
        with self.engine.connect() as db:
            rows = db.execute(
                select(prediction_records).order_by(prediction_records.c.kickoff_at, prediction_records.c.id)
            ).mappings().all()
        return [self._record_from_row(row) for row in rows]

    def get_prediction(self, identity: SportyBetSelectionIdentity) -> PredictionRecord | None:
        self._ensure_schema()
        with self.engine.connect() as db:
            row = db.execute(
                select(prediction_records).where(_identity_condition(identity))
            ).mappings().first()
        return self._record_from_row(row) if row is not None else None

    def get_predictions_by_identities(
        self,
        identities: list[SportyBetSelectionIdentity],
    ) -> list[PredictionRecord]:
        if not identities:
            return []
        self._ensure_schema()
        conditions = [_identity_condition(identity) for identity in identities]
        with self.engine.connect() as db:
            rows = db.execute(
                select(prediction_records).where(or_(*conditions))
            ).mappings().all()
        return [self._record_from_row(row) for row in rows]

    def list_settlement_candidates(self) -> list[PredictionRecord]:
        self._ensure_schema()
        monitorable_statuses = [
            PredictionStatus.pending.value,
            PredictionStatus.live.value,
            PredictionStatus.postponed.value,
            PredictionStatus.unresolved.value,
        ]
        with self.engine.connect() as db:
            rows = db.execute(
                select(prediction_records)
                .where(prediction_records.c.prediction_status.in_(monitorable_statuses))
                .order_by(prediction_records.c.kickoff_at, prediction_records.c.id)
            ).mappings().all()
        return [self._record_from_row(row) for row in rows]

    def apply_settlement_update(
        self,
        settlement_update: PredictionSettlementUpdate,
    ) -> tuple[PredictionSettlementApplyOutcome, PredictionRecord | None]:
        self._ensure_schema()
        settled_statuses = {
            PredictionStatus.settled_win.value,
            PredictionStatus.settled_miss.value,
            PredictionStatus.won.value,
            PredictionStatus.lost.value,
        }
        non_result_final_statuses = {
            PredictionStatus.cancelled.value,
            PredictionStatus.abandoned.value,
            PredictionStatus.void.value,
        }
        now = datetime.now(timezone.utc)
        values = {
            'prediction_status': settlement_update.prediction_status.value,
            'live_status': settlement_update.live_status,
            'current_home_goals': settlement_update.current_home_goals,
            'current_away_goals': settlement_update.current_away_goals,
            'last_score_update_at': now,
            'updated_at': now,
        }
        if settlement_update.prediction_status in {
            PredictionStatus.settled_win,
            PredictionStatus.settled_miss,
        }:
            values.update(
                actual_goals=settlement_update.actual_goals,
                actual_result=settlement_update.actual_result,
                settled_at=settlement_update.settled_at or now,
            )

        with self.engine.begin() as db:
            result = db.execute(
                update(prediction_records)
                .where(
                    prediction_records.c.id == settlement_update.prediction_id,
                    prediction_records.c.prediction_status.notin_(settled_statuses),
                )
                .values(**values)
            )
            if result.rowcount:
                row = db.execute(
                    select(prediction_records).where(
                        prediction_records.c.id == settlement_update.prediction_id
                    )
                ).mappings().first()
                return PredictionSettlementApplyOutcome.updated, (
                    self._record_from_row(row) if row is not None else None
                )

            row = db.execute(
                select(prediction_records).where(
                    prediction_records.c.id == settlement_update.prediction_id
                )
            ).mappings().first()
            if row is None:
                return PredictionSettlementApplyOutcome.conflict, None

            current_status = PredictionStatus(row['prediction_status'])
            if current_status in {PredictionStatus.settled_win, PredictionStatus.settled_miss}:
                outcome = (
                    PredictionSettlementApplyOutcome.skipped_already_settled
                    if current_status == settlement_update.prediction_status
                    else PredictionSettlementApplyOutcome.conflict
                )
            elif current_status in non_result_final_statuses:
                outcome = (
                    PredictionSettlementApplyOutcome.skipped_already_settled
                    if current_status == settlement_update.prediction_status
                    else PredictionSettlementApplyOutcome.conflict
                )
            else:
                outcome = PredictionSettlementApplyOutcome.conflict
            return outcome, self._record_from_row(row)

    def attach_booking_metadata(
        self,
        identities: list[SportyBetSelectionIdentity],
        *,
        booking_pool: str,
        booking_code: str,
        booking_batch_identity: str,
        booking_created_at: datetime,
    ) -> int:
        if not identities:
            return 0
        self._ensure_schema()
        conditions = [_identity_condition(identity) for identity in identities]
        with self.engine.begin() as db:
            result = db.execute(
                update(prediction_records)
                .where(or_(*conditions))
                .values(
                    booking_pool=booking_pool,
                    booking_code=booking_code,
                    booking_batch_identity=booking_batch_identity,
                    booking_created_at=booking_created_at,
                    updated_at=datetime.now(timezone.utc),
                )
            )
        return result.rowcount or 0

    def get_message_delivery(
        self,
        delivery_identity: str,
    ) -> PredictionMessageDeliveryRecord | None:
        self._ensure_schema()
        with self.engine.connect() as db:
            row = db.execute(
                select(prediction_message_deliveries).where(
                    prediction_message_deliveries.c.delivery_identity == delivery_identity
                )
            ).mappings().first()
        return self._message_record_from_row(row) if row is not None else None

    def claim_message_delivery(
        self,
        *,
        generation_identity: str,
        category: PredictionMessageCategory,
        delivery_identity: str,
        input_identity: str,
        telegram_user_id: int | None = None,
        batch_identities: list[str],
        booking_codes: list[str],
        payload: dict[str, Any],
        now: datetime,
        ttl_seconds: float,
    ) -> tuple[PredictionMessageDeliveryRecord, bool]:
        self._ensure_schema()
        values = {
            'generation_identity': generation_identity,
            'category': category.value,
            'delivery_identity': delivery_identity,
            'input_identity': input_identity,
            'telegram_user_id': telegram_user_id,
            'batch_identities': batch_identities,
            'booking_codes': booking_codes,
            'payload': payload,
            'status': PredictionMessageDeliveryStatus.pending.value,
            'retry_count': 0,
            'claim_expires_at': now + timedelta(seconds=ttl_seconds),
            'created_at': now,
            'updated_at': now,
        }
        try:
            with self.engine.begin() as db:
                db.execute(prediction_message_deliveries.insert().values(**values))
        except IntegrityError:
            existing = self.get_message_delivery(delivery_identity)
            if existing is None:
                raise
            if existing.status in {
                PredictionMessageDeliveryStatus.delivered,
                PredictionMessageDeliveryStatus.undeliverable,
            }:
                return existing, False
            with self.engine.begin() as db:
                result = db.execute(
                    update(prediction_message_deliveries)
                    .where(
                        prediction_message_deliveries.c.id == existing.id,
                        prediction_message_deliveries.c.status
                        .notin_([
                            PredictionMessageDeliveryStatus.delivered.value,
                            PredictionMessageDeliveryStatus.undeliverable.value,
                        ]),
                        or_(
                            prediction_message_deliveries.c.claim_expires_at.is_(None),
                            prediction_message_deliveries.c.claim_expires_at <= now,
                        ),
                    )
                    .values(
                        status=PredictionMessageDeliveryStatus.pending.value,
                        telegram_user_id=(
                            telegram_user_id
                            if telegram_user_id is not None
                            else prediction_message_deliveries.c.telegram_user_id
                        ),
                        claim_expires_at=now + timedelta(seconds=ttl_seconds),
                        retry_count=prediction_message_deliveries.c.retry_count + 1,
                        updated_at=now,
                    )
                )
            current = self.get_message_delivery(delivery_identity)
            if current is None:
                raise RuntimeError('Prediction message delivery disappeared')
            return current, bool(result.rowcount)

        created = self.get_message_delivery(delivery_identity)
        if created is None:
            raise RuntimeError('Prediction message delivery was not created')
        return created, True

    def mark_message_delivery_delivered(
        self,
        delivery_identity: str,
        *,
        sent_at: datetime,
    ) -> PredictionMessageDeliveryRecord:
        self._ensure_schema()
        with self.engine.begin() as db:
            db.execute(
                update(prediction_message_deliveries)
                .where(
                    prediction_message_deliveries.c.delivery_identity == delivery_identity,
                    prediction_message_deliveries.c.status
                    != PredictionMessageDeliveryStatus.delivered.value,
                )
                .values(
                    status=PredictionMessageDeliveryStatus.delivered.value,
                    sent_at=sent_at,
                    error_summary=None,
                    claim_expires_at=None,
                    updated_at=sent_at,
                )
            )
        delivered = self.get_message_delivery(delivery_identity)
        if delivered is None:
            raise RuntimeError('Prediction message delivery disappeared')
        return delivered

    def mark_message_delivery_failed(
        self,
        delivery_identity: str,
        *,
        now: datetime,
        error_summary: str,
    ) -> PredictionMessageDeliveryRecord:
        self._ensure_schema()
        with self.engine.begin() as db:
            db.execute(
                update(prediction_message_deliveries)
                .where(
                    prediction_message_deliveries.c.delivery_identity == delivery_identity,
                    prediction_message_deliveries.c.status
                    != PredictionMessageDeliveryStatus.delivered.value,
                )
                .values(
                    status=PredictionMessageDeliveryStatus.failed.value,
                    error_summary=error_summary,
                    claim_expires_at=None,
                    updated_at=now,
                )
            )
        failed = self.get_message_delivery(delivery_identity)
        if failed is None:
            raise RuntimeError('Prediction message delivery disappeared')
        return failed

    def mark_message_delivery_undeliverable(
        self,
        delivery_identity: str,
        *,
        now: datetime,
        error_summary: str,
    ) -> PredictionMessageDeliveryRecord:
        self._ensure_schema()
        with self.engine.begin() as db:
            db.execute(
                update(prediction_message_deliveries)
                .where(
                    prediction_message_deliveries.c.delivery_identity == delivery_identity,
                    prediction_message_deliveries.c.status
                    != PredictionMessageDeliveryStatus.delivered.value,
                )
                .values(
                    status=PredictionMessageDeliveryStatus.undeliverable.value,
                    error_summary=error_summary,
                    claim_expires_at=None,
                    updated_at=now,
                )
            )
        undeliverable = self.get_message_delivery(delivery_identity)
        if undeliverable is None:
            raise RuntimeError('Prediction message delivery disappeared')
        return undeliverable

    def claim_generation(
        self,
        *,
        generation_identity: str,
        timezone_name: str,
        today: date,
        tomorrow: date,
        now: datetime,
        ttl_seconds: float,
    ) -> tuple[PredictionGenerationDeliveryRecord, bool]:
        self._ensure_schema()
        current = _utc(now)
        expires_at = current + timedelta(seconds=ttl_seconds)

        for _ in range(2):
            try:
                with self.engine.begin() as db:
                    db.execute(
                        prediction_generation_deliveries.insert().values(
                            claim_identity=generation_identity,
                            generation_identity=generation_identity,
                            timezone_name=timezone_name,
                            today=today,
                            tomorrow=tomorrow,
                            status='generating',
                            qualifying_batch_identities=[],
                            prediction_batch_identities=[],
                            booking_codes=[],
                            claim_expires_at=expires_at,
                            created_at=current,
                            updated_at=current,
                        )
                    )
                return self._generation_record_by_claim(generation_identity), True
            except IntegrityError:
                with self.engine.begin() as db:
                    row = db.execute(
                        select(prediction_generation_deliveries).where(
                            prediction_generation_deliveries.c.claim_identity
                            == generation_identity
                        )
                    ).mappings().first()
                    if row is None:
                        continue
                    record = self._generation_record_from_row(row)
                    if record.claim_expires_at is not None and record.claim_expires_at > current:
                        return record, False
                    claimed = db.execute(
                        update(prediction_generation_deliveries)
                        .where(
                            prediction_generation_deliveries.c.id == row['id'],
                            prediction_generation_deliveries.c.claim_identity
                            == generation_identity,
                            prediction_generation_deliveries.c.claim_expires_at <= current,
                        )
                        .values(
                            status='generating',
                            error_summary=None,
                            claim_expires_at=expires_at,
                            updated_at=current,
                        )
                    )
                    if claimed.rowcount:
                        refreshed = db.execute(
                            select(prediction_generation_deliveries).where(
                                prediction_generation_deliveries.c.id == row['id']
                            )
                        ).mappings().first()
                        return self._generation_record_from_row(refreshed), True
                    refreshed = db.execute(
                        select(prediction_generation_deliveries).where(
                            prediction_generation_deliveries.c.id == row['id']
                        )
                    ).mappings().first()
                    return self._generation_record_from_row(refreshed), False

        raise RuntimeError('Unable to claim prediction generation')

    def discard_generation_claim(self, record_id: int) -> None:
        self._ensure_schema()
        with self.engine.begin() as db:
            db.execute(
                delete(prediction_generation_deliveries).where(
                    prediction_generation_deliveries.c.id == record_id
                )
            )

    def find_generation_by_input(
        self,
        *,
        generation_identity: str,
        input_identity: str,
    ) -> PredictionGenerationDeliveryRecord | None:
        self._ensure_schema()
        with self.engine.connect() as db:
            row = db.execute(
                select(prediction_generation_deliveries)
                .where(
                    prediction_generation_deliveries.c.generation_identity
                    == generation_identity,
                    prediction_generation_deliveries.c.input_identity == input_identity,
                )
                .order_by(desc(prediction_generation_deliveries.c.id))
                .limit(1)
            ).mappings().first()
        return self._generation_record_from_row(row) if row is not None else None

    def find_generation_by_delivery(
        self,
        delivery_identity: str,
    ) -> PredictionGenerationDeliveryRecord | None:
        self._ensure_schema()
        with self.engine.connect() as db:
            row = db.execute(
                select(prediction_generation_deliveries).where(
                    prediction_generation_deliveries.c.delivery_identity
                    == delivery_identity
                )
            ).mappings().first()
        return self._generation_record_from_row(row) if row is not None else None

    def mark_generation_generated(
        self,
        record_id: int,
        *,
        input_identity: str,
        delivery_identity: str,
        generated_at: datetime,
        result: Any,
        qualifying_batch_identities: list[str],
        prediction_batch_identities: list[str],
        booking_codes: list[str],
    ) -> PredictionGenerationDeliveryRecord:
        self._ensure_schema()
        current = _utc(generated_at)
        with self.engine.begin() as db:
            db.execute(
                update(prediction_generation_deliveries)
                .where(prediction_generation_deliveries.c.id == record_id)
                .values(
                    input_identity=input_identity,
                    delivery_identity=delivery_identity,
                    generated_at=current,
                    status='generated',
                    error_summary=None,
                    result_snapshot=result.model_dump(mode='json'),
                    qualifying_batch_identities=qualifying_batch_identities,
                    prediction_batch_identities=prediction_batch_identities,
                    booking_codes=booking_codes,
                    updated_at=current,
                )
            )
        return self.get_generation_record(record_id)

    def mark_generation_delivery_pending(
        self,
        record_id: int,
        *,
        now: datetime,
    ) -> PredictionGenerationDeliveryRecord:
        return self._update_generation_status(
            record_id,
            status='delivery_pending',
            now=now,
        )

    def mark_generation_delivered(
        self,
        record_id: int,
        *,
        delivered_at: datetime,
    ) -> PredictionGenerationDeliveryRecord:
        current = _utc(delivered_at)
        self._ensure_schema()
        with self.engine.begin() as db:
            db.execute(
                update(prediction_generation_deliveries)
                .where(prediction_generation_deliveries.c.id == record_id)
                .values(
                    status='delivered',
                    delivered_at=current,
                    claim_identity=None,
                    error_summary=None,
                    updated_at=current,
                )
            )
        return self.get_generation_record(record_id)

    def mark_generation_delivery_failed(
        self,
        record_id: int,
        *,
        now: datetime,
        error_summary: str,
    ) -> PredictionGenerationDeliveryRecord:
        current = _utc(now)
        self._ensure_schema()
        with self.engine.begin() as db:
            db.execute(
                update(prediction_generation_deliveries)
                .where(prediction_generation_deliveries.c.id == record_id)
                .values(
                    status='delivery_failed',
                    claim_identity=None,
                    error_summary=error_summary,
                    updated_at=current,
                )
            )
        return self.get_generation_record(record_id)

    def mark_generation_failed(
        self,
        record_id: int,
        *,
        now: datetime,
        error_summary: str,
    ) -> PredictionGenerationDeliveryRecord:
        current = _utc(now)
        self._ensure_schema()
        with self.engine.begin() as db:
            db.execute(
                update(prediction_generation_deliveries)
                .where(prediction_generation_deliveries.c.id == record_id)
                .values(
                    status='generation_failed',
                    claim_identity=None,
                    error_summary=error_summary,
                    updated_at=current,
                )
            )
        return self.get_generation_record(record_id)

    def get_generation_record(
        self,
        record_id: int,
    ) -> PredictionGenerationDeliveryRecord:
        self._ensure_schema()
        with self.engine.connect() as db:
            row = db.execute(
                select(prediction_generation_deliveries).where(
                    prediction_generation_deliveries.c.id == record_id
                )
            ).mappings().first()
        if row is None:
            raise ValueError(f'Prediction generation record {record_id} was not found')
        return self._generation_record_from_row(row)

    def _generation_record_by_claim(
        self,
        claim_identity: str,
    ) -> PredictionGenerationDeliveryRecord:
        with self.engine.connect() as db:
            row = db.execute(
                select(prediction_generation_deliveries).where(
                    prediction_generation_deliveries.c.claim_identity == claim_identity
                )
            ).mappings().first()
        if row is None:
            raise ValueError('Prediction generation claim was not found')
        return self._generation_record_from_row(row)

    def _update_generation_status(
        self,
        record_id: int,
        *,
        status: str,
        now: datetime,
    ) -> PredictionGenerationDeliveryRecord:
        current = _utc(now)
        self._ensure_schema()
        with self.engine.begin() as db:
            db.execute(
                update(prediction_generation_deliveries)
                .where(prediction_generation_deliveries.c.id == record_id)
                .values(status=status, updated_at=current)
            )
        return self.get_generation_record(record_id)

    @staticmethod
    def _generation_record_from_row(row: Any) -> PredictionGenerationDeliveryRecord:
        from app.schemas.prediction_booking import PredictionBookingResult

        return PredictionGenerationDeliveryRecord(
            id=row['id'],
            generation_identity=row['generation_identity'],
            input_identity=row['input_identity'],
            delivery_identity=row['delivery_identity'],
            timezone_name=row['timezone_name'],
            today=row['today'],
            tomorrow=row['tomorrow'],
            generated_at=_utc(row['generated_at']),
            status=row['status'],
            delivered_at=_utc(row['delivered_at']),
            error_summary=row['error_summary'],
            result=(
                PredictionBookingResult.model_validate(row['result_snapshot'])
                if row['result_snapshot'] is not None
                else None
            ),
            qualifying_batch_identities=row['qualifying_batch_identities'] or [],
            prediction_batch_identities=row['prediction_batch_identities'] or [],
            booking_codes=row['booking_codes'] or [],
            claim_expires_at=_utc(row['claim_expires_at']),
            created_at=_utc(row['created_at']),
            updated_at=_utc(row['updated_at']),
        )

    @staticmethod
    def _message_record_from_row(row: Any) -> PredictionMessageDeliveryRecord:
        return PredictionMessageDeliveryRecord(
            id=row['id'],
            generation_identity=row['generation_identity'],
            category=PredictionMessageCategory(row['category']),
            delivery_identity=row['delivery_identity'],
            input_identity=row['input_identity'],
            telegram_user_id=row['telegram_user_id'],
            batch_identities=row['batch_identities'] or [],
            booking_codes=row['booking_codes'] or [],
            payload=row['payload'] or {},
            status=PredictionMessageDeliveryStatus(row['status']),
            sent_at=_utc(row['sent_at']),
            error_summary=row['error_summary'],
            retry_count=row['retry_count'],
            claim_expires_at=_utc(row['claim_expires_at']),
            created_at=_utc(row['created_at']),
            updated_at=_utc(row['updated_at']),
        )

    @staticmethod
    def _record_from_row(row: Any) -> PredictionRecord:
        return PredictionRecord(
            id=row['id'],
            event_id=row['event_id'],
            sport_id=row['sport_id'],
            market_id=row['market_id'],
            outcome_id=row['outcome_id'],
            product_id=row['product_id'],
            specifier=row['specifier'],
            home_team=row['home_team'],
            away_team=row['away_team'],
            competition=row['competition'],
            kickoff_at=_utc(row['kickoff_at']),
            prediction_market=row['prediction_market'],
            odds_at_prediction=row['odds_at_prediction'],
            baseline_score=row['baseline_score'],
            evidence_score=row['evidence_score'],
            evidence_quality=(
                EvidenceQuality(row['evidence_quality'])
                if row['evidence_quality'] is not None
                else None
            ),
            model_score=row['model_score'],
            confidence=row['confidence'],
            selection_reason=row['selection_reason'],
            provider_probability=row['provider_probability'],
            provider_probability_source=row['provider_probability_source'],
            explanation=(
                PredictionExplanation.model_validate(row['explanation'])
                if row['explanation'] is not None
                else None
            ),
            booking_pool=row['booking_pool'],
            booking_code=row['booking_code'],
            booking_batch_identity=row['booking_batch_identity'],
            booking_created_at=_utc(row['booking_created_at']),
            feature_snapshot=PredictionFeatureSnapshot.model_validate(row['feature_snapshot']),
            prediction_status=PredictionStatus(row['prediction_status']),
            live_status=row['live_status'],
            current_home_goals=row['current_home_goals'],
            current_away_goals=row['current_away_goals'],
            last_score_update_at=_utc(row['last_score_update_at']),
            actual_goals=row['actual_goals'],
            actual_result=row['actual_result'],
            settled_at=_utc(row['settled_at']),
            model_version=row['model_version'],
            created_at=_utc(row['created_at']),
            updated_at=_utc(row['updated_at']),
        )


prediction_store = PredictionStore()
