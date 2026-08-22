from __future__ import annotations

import json
from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Column, Date, DateTime, Integer, MetaData, String, Table, Text, UniqueConstraint, create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

from app.config.settings import get_settings
from app.schemas.forebet_draw_window import DrawWindowDay, DrawWindowMatch
from app.services.history_store import _database_url

metadata = MetaData()
daily = Table("forebet_draw_daily_bookings", metadata, Column("id", Integer, primary_key=True), Column("prediction_date", Date, nullable=False, unique=True), Column("booking_code", String(64), nullable=False), Column("status", String(16), nullable=False), Column("matches_json", Text, nullable=False), Column("source_urls_json", Text, nullable=False), Column("diagnostics_json", Text, nullable=False), Column("created_at", DateTime(timezone=True), nullable=False), Column("updated_at", DateTime(timezone=True), nullable=False))
prebooking = Table("forebet_draw_prebooking_candidates", metadata, Column("id", Integer, primary_key=True), Column("prediction_date", Date, nullable=False, unique=True), Column("candidates_json", Text, nullable=False), Column("diagnostics_json", Text, nullable=False), Column("updated_at", DateTime(timezone=True), nullable=False))
revisions = Table("forebet_draw_booking_revisions", metadata, Column("id", Integer, primary_key=True), Column("prediction_date", Date, nullable=False), Column("booking_code", String(64), nullable=False), Column("matches_json", Text, nullable=False), Column("is_current", Boolean, nullable=False), Column("created_at", DateTime(timezone=True), nullable=False), UniqueConstraint("prediction_date", "booking_code", name="uq_draw_revision_date_code"))


class ForebetDrawStore:
    def __init__(self, path: str | None = None, *, database_url: str | None = None):
        settings = get_settings(); configured = database_url if database_url is not None else (None if path else settings.database_url)
        url = _database_url(path, configured); options = {"pool_pre_ping": True}
        if url.startswith("sqlite:"): options["poolclass"] = NullPool
        self.engine: Engine = create_engine(url, **options); self._initialized = False

    def _ensure(self):
        if not self._initialized: metadata.create_all(self.engine); self._initialized = True

    @staticmethod
    def _dump_matches(matches): return json.dumps([m.model_dump(mode="json") for m in matches])

    def list_active(self) -> list[DrawWindowDay]:
        self._ensure()
        with self.engine.connect() as db: rows = db.execute(select(daily).where(daily.c.status == "active").order_by(daily.c.prediction_date)).all()
        return [DrawWindowDay(prediction_date=r.prediction_date, booking_code=r.booking_code, selection_count=len(json.loads(r.matches_json)), status=r.status, matches=[DrawWindowMatch.model_validate(x) for x in json.loads(r.matches_json)], source_urls=json.loads(r.source_urls_json), diagnostics=json.loads(r.diagnostics_json), created_at=r.created_at, last_updated=r.updated_at) for r in rows]

    def promote(self, prediction_date: date, booking_code: str, matches: list[DrawWindowMatch], source_urls: list[str], diagnostics: list[str]):
        self._ensure(); now = datetime.now(timezone.utc); encoded = self._dump_matches(matches)
        with self.engine.begin() as db:
            row = db.execute(select(daily).where(daily.c.prediction_date == prediction_date)).first()
            if row:
                db.execute(revisions.update().where(revisions.c.prediction_date == prediction_date).values(is_current=False))
                db.execute(daily.update().where(daily.c.prediction_date == prediction_date).values(booking_code=booking_code, status="active", matches_json=encoded, source_urls_json=json.dumps(source_urls), diagnostics_json=json.dumps(diagnostics), updated_at=now))
            else:
                db.execute(daily.insert().values(prediction_date=prediction_date, booking_code=booking_code, status="active", matches_json=encoded, source_urls_json=json.dumps(source_urls), diagnostics_json=json.dumps(diagnostics), created_at=now, updated_at=now))
            db.execute(revisions.insert().values(prediction_date=prediction_date, booking_code=booking_code, matches_json=encoded, is_current=True, created_at=now))

    def complete_not_in(self, active_dates: set[date]):
        self._ensure(); now = datetime.now(timezone.utc)
        with self.engine.begin() as db:
            db.execute(daily.update().where(daily.c.status == "active", daily.c.prediction_date.not_in(active_dates)).values(status="complete", updated_at=now))
            db.execute(revisions.update().where(revisions.c.is_current.is_(True), revisions.c.prediction_date.not_in(active_dates)).values(is_current=False))

    def save_prebooking(self, prediction_date: date, candidates: list[dict], diagnostics: dict):
        self._ensure(); now = datetime.now(timezone.utc)
        values = {"prediction_date": prediction_date, "candidates_json": json.dumps(candidates, default=str), "diagnostics_json": json.dumps(diagnostics, default=str), "updated_at": now}
        with self.engine.begin() as db:
            row = db.execute(select(prebooking).where(prebooking.c.prediction_date == prediction_date)).first()
            if row:
                db.execute(prebooking.update().where(prebooking.c.prediction_date == prediction_date).values(**values))
            else:
                db.execute(prebooking.insert().values(**values))

    def list_prebooking(self) -> list[dict]:
        self._ensure()
        with self.engine.connect() as db:
            rows = db.execute(select(prebooking).order_by(prebooking.c.prediction_date)).all()
        return [{"prediction_date": row.prediction_date, "candidates": json.loads(row.candidates_json), "diagnostics": json.loads(row.diagnostics_json), "updated_at": row.updated_at} for row in rows]


forebet_draw_store = ForebetDrawStore()
