from __future__ import annotations

import math
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    create_engine,
    delete,
    select,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

from app.config.settings import get_settings
from app.schemas.booking import BookingResponse

HISTORY_LIMIT = 50
metadata = MetaData()

booking_history = Table(
    "booking_history",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("telegram_user_id", BigInteger, nullable=False),
    Column("booking_code", String(64), nullable=False),
    Column("loaded_at", DateTime(timezone=True), nullable=False),
    Column("selection_count", Integer),
    Column("remaining_odds", Float),
    UniqueConstraint("telegram_user_id", "booking_code", name="uq_history_user_code"),
)
Index("idx_history_user_loaded", booking_history.c.telegram_user_id, booking_history.c.loaded_at)

selection_odds_snapshots = Table(
    "selection_odds_snapshots",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("booking_code", String(64), nullable=False),
    Column("event_id", String(128), nullable=False),
    Column("market_id", String(64), nullable=False),
    Column("outcome_id", String(64), nullable=False),
    Column("specifier", String(256), nullable=False, default=""),
    Column("observed_odds", Float, nullable=False),
    Column("observed_at", DateTime(timezone=True), nullable=False),
    Column("status_at_observation", String(32), nullable=False),
    UniqueConstraint(
        "booking_code", "event_id", "market_id", "outcome_id", "specifier",
        name="uq_selection_odds_identity",
    ),
)


def _database_url(path: str | None = None, database_url: str | None = None) -> str:
    if database_url:
        if database_url.startswith("postgres://"):
            return "postgresql+psycopg2://" + database_url.removeprefix("postgres://")
        if database_url.startswith("postgresql://"):
            return "postgresql+psycopg2://" + database_url.removeprefix("postgresql://")
        return database_url
    sqlite_path = path or get_settings().history_database_path
    return f"sqlite:///{sqlite_path}"


class HistoryStore:
    """Persistent history and immutable first-observed selection odds."""

    def __init__(self, path: str | None = None, *, database_url: str | None = None) -> None:
        settings = get_settings()
        configured_url = database_url if database_url is not None else (None if path else settings.database_url)
        url = _database_url(path, configured_url)
        options = {"pool_pre_ping": True}
        if url.startswith("sqlite:"):
            options["poolclass"] = NullPool
        self.engine: Engine = create_engine(url, **options)
        self._initialized = False

    def _ensure_schema(self) -> None:
        if not self._initialized:
            metadata.create_all(self.engine)
            self._initialized = True

    def upsert(self, user_id: int, booking_code: str, selection_count: int, remaining_odds: float) -> None:
        self._ensure_schema()
        code = booking_code.upper()
        now = datetime.now(timezone.utc)
        with self.engine.begin() as db:
            existing = db.execute(select(booking_history.c.id).where(
                booking_history.c.telegram_user_id == user_id,
                booking_history.c.booking_code == code,
            )).scalar_one_or_none()
            values = dict(loaded_at=now, selection_count=selection_count, remaining_odds=remaining_odds)
            if existing is None:
                db.execute(booking_history.insert().values(telegram_user_id=user_id, booking_code=code, **values))
            else:
                db.execute(booking_history.update().where(booking_history.c.id == existing).values(**values))

            keep_ids = select(booking_history.c.id).where(
                booking_history.c.telegram_user_id == user_id
            ).order_by(booking_history.c.loaded_at.desc(), booking_history.c.id.desc()).limit(HISTORY_LIMIT)
            db.execute(delete(booking_history).where(
                booking_history.c.telegram_user_id == user_id,
                booking_history.c.id.not_in(keep_ids),
            ))

    def list(self, user_id: int) -> list[dict]:
        self._ensure_schema()
        query = select(
            booking_history.c.id,
            booking_history.c.booking_code,
            booking_history.c.loaded_at,
            booking_history.c.selection_count,
            booking_history.c.remaining_odds,
        ).where(booking_history.c.telegram_user_id == user_id).order_by(
            booking_history.c.loaded_at.desc(), booking_history.c.id.desc()
        ).limit(HISTORY_LIMIT)
        with self.engine.connect() as db:
            return [dict(row._mapping) for row in db.execute(query)]

    def delete(self, user_id: int, history_id: int) -> bool:
        self._ensure_schema()
        with self.engine.begin() as db:
            result = db.execute(delete(booking_history).where(booking_history.c.id == history_id, booking_history.c.telegram_user_id == user_id))
        return result.rowcount > 0

    def apply_observed_odds(self, booking: BookingResponse) -> BookingResponse:
        """Persist active odds once; replace ended odds only from that snapshot."""
        self._ensure_schema()
        code = booking.booking_code.upper()
        now = datetime.now(timezone.utc)
        with self.engine.begin() as db:
            for selection in booking.selections:
                identity = (
                    selection_odds_snapshots.c.booking_code == code,
                    selection_odds_snapshots.c.event_id == selection.event_id,
                    selection_odds_snapshots.c.market_id == selection.market_id,
                    selection_odds_snapshots.c.outcome_id == selection.outcome_id,
                    selection_odds_snapshots.c.specifier == (selection.specifier or ""),
                )
                observed = db.execute(select(selection_odds_snapshots.c.observed_odds).where(*identity)).scalar_one_or_none()

                if selection.game_status in {"upcoming", "live"}:
                    odds = selection.odds
                    if observed is None and odds is not None and math.isfinite(odds) and odds > 0:
                        try:
                            with db.begin_nested():
                                db.execute(selection_odds_snapshots.insert().values(
                                    booking_code=code,
                                    event_id=selection.event_id,
                                    market_id=selection.market_id,
                                    outcome_id=selection.outcome_id,
                                    specifier=selection.specifier or "",
                                    observed_odds=odds,
                                    observed_at=now,
                                    status_at_observation=selection.game_status,
                                ))
                        except IntegrityError:
                            pass
                    selection.odds_source = "sportybet_current"
                else:
                    selection.odds = observed
                    selection.odds_source = "preserved_observation" if observed is not None else "unavailable"
        return booking


history_store = HistoryStore()
