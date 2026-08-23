from __future__ import annotations

import json
from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Column, Date, DateTime, Integer, MetaData, String, Table, Text, UniqueConstraint, create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

from app.config.settings import get_settings
from app.schemas.forebet_draw_window import DrawCompilation, DrawWindowDay, DrawWindowMatch
from app.services.history_store import _database_url

metadata = MetaData()
daily = Table("forebet_draw_daily_bookings", metadata, Column("id", Integer, primary_key=True), Column("prediction_date", Date, nullable=False, unique=True), Column("booking_code", String(64), nullable=False), Column("status", String(16), nullable=False), Column("matches_json", Text, nullable=False), Column("source_urls_json", Text, nullable=False), Column("diagnostics_json", Text, nullable=False), Column("created_at", DateTime(timezone=True), nullable=False), Column("updated_at", DateTime(timezone=True), nullable=False))
prebooking = Table("forebet_draw_prebooking_candidates", metadata, Column("id", Integer, primary_key=True), Column("prediction_date", Date, nullable=False, unique=True), Column("candidates_json", Text, nullable=False), Column("diagnostics_json", Text, nullable=False), Column("updated_at", DateTime(timezone=True), nullable=False))
compilation = Table("forebet_draw_compilation", metadata, Column("id", Integer, primary_key=True), Column("booking_code", String(64), nullable=False), Column("identity", String(128), nullable=False, default=""), Column("matches_json", Text, nullable=False), Column("prediction_dates_json", Text, nullable=False), Column("diagnostics_json", Text, nullable=False, default="[]"), Column("status", String(16), nullable=False), Column("created_at", DateTime(timezone=True), nullable=False), Column("updated_at", DateTime(timezone=True), nullable=False))
revisions = Table("forebet_draw_booking_revisions", metadata, Column("id", Integer, primary_key=True), Column("prediction_date", Date, nullable=False), Column("booking_code", String(64), nullable=False), Column("matches_json", Text, nullable=False), Column("is_current", Boolean, nullable=False), Column("created_at", DateTime(timezone=True), nullable=False), UniqueConstraint("prediction_date", "booking_code", name="uq_draw_revision_date_code"))
raw_snapshots = Table("forebet_raw_snapshots", metadata, Column("id", Integer, primary_key=True), Column("source", String(64), nullable=False), Column("prediction_date", Date, nullable=False), Column("source_url", Text, nullable=False), Column("retrieved_at", DateTime(timezone=True), nullable=False), Column("raw_content", Text, nullable=False), Column("content_hash", String(64), nullable=False))


class ForebetDrawStore:
    def __init__(self, path: str | None = None, *, database_url: str | None = None):
        settings = get_settings(); configured = database_url if database_url is not None else (None if path else settings.database_url)
        url = _database_url(path, configured); options = {"pool_pre_ping": True}
        if url.startswith("sqlite:"): options["poolclass"] = NullPool
        self.engine: Engine = create_engine(url, **options); self._initialized = False

    def _ensure(self):
        if not self._initialized:
            metadata.create_all(self.engine)
            # Metadata creation is intentionally retained; add the one new nullable
            # identity column for databases created by an earlier application build.
            with self.engine.begin() as db:
                try:
                    db.exec_driver_sql("ALTER TABLE forebet_draw_compilation ADD COLUMN identity VARCHAR(128) NOT NULL DEFAULT ''")
                except Exception:
                    pass
                try:
                    db.exec_driver_sql("ALTER TABLE forebet_draw_compilation ADD COLUMN diagnostics_json TEXT NOT NULL DEFAULT '[]'")
                except Exception:
                    pass
            self._initialized = True

    @staticmethod
    def _dump_matches(matches): return json.dumps([m.model_dump(mode="json") for m in matches])

    def list_active(self) -> list[DrawWindowDay]:
        self._ensure()
        with self.engine.connect() as db: rows = db.execute(select(daily).where(daily.c.status.in_(("active", "unavailable", "error"))).order_by(daily.c.prediction_date)).all()
        return [DrawWindowDay(prediction_date=r.prediction_date, booking_code=r.booking_code or None, selection_count=len(json.loads(r.matches_json)), status=r.status, matches=[DrawWindowMatch.model_validate(x) for x in json.loads(r.matches_json)], source_urls=json.loads(r.source_urls_json), diagnostics=json.loads(r.diagnostics_json), created_at=r.created_at, last_updated=r.updated_at) for r in rows]

    def promote(self, prediction_date: date, booking_code: str | None, matches: list[DrawWindowMatch], source_urls: list[str], diagnostics: list[str], status: str = "active"):
        self._ensure(); now = datetime.now(timezone.utc); encoded = self._dump_matches(matches); stored_code = booking_code or ""
        with self.engine.begin() as db:
            row = db.execute(select(daily).where(daily.c.prediction_date == prediction_date)).first()
            if row:
                db.execute(revisions.update().where(revisions.c.prediction_date == prediction_date).values(is_current=False))
                db.execute(daily.update().where(daily.c.prediction_date == prediction_date).values(booking_code=stored_code, status=status, matches_json=encoded, source_urls_json=json.dumps(source_urls), diagnostics_json=json.dumps(diagnostics), updated_at=now))
            else:
                db.execute(daily.insert().values(prediction_date=prediction_date, booking_code=stored_code, status=status, matches_json=encoded, source_urls_json=json.dumps(source_urls), diagnostics_json=json.dumps(diagnostics), created_at=now, updated_at=now))
            if booking_code:
                db.execute(revisions.insert().values(prediction_date=prediction_date, booking_code=booking_code, matches_json=encoded, is_current=True, created_at=now))

    def complete_not_in(self, active_dates: set[date]):
        self._ensure(); now = datetime.now(timezone.utc)
        with self.engine.begin() as db:
            db.execute(daily.update().where(daily.c.status.in_(("active", "unavailable", "error")), daily.c.prediction_date.not_in(active_dates)).values(status="complete", updated_at=now))
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

    def save_raw_snapshot(self, prediction_date: date, source_url: str, raw_content: str, *, source: str = "forebet"):
        import hashlib
        self._ensure()
        with self.engine.begin() as db:
            db.execute(raw_snapshots.insert().values(source=source, prediction_date=prediction_date, source_url=source_url, retrieved_at=datetime.now(timezone.utc), raw_content=raw_content, content_hash=hashlib.sha256(raw_content.encode()).hexdigest()))

    def list_prebooking(self) -> list[dict]:
        self._ensure()
        with self.engine.connect() as db:
            rows = db.execute(select(prebooking).order_by(prebooking.c.prediction_date)).all()
        return [{"prediction_date": row.prediction_date, "candidates": json.loads(row.candidates_json), "diagnostics": json.loads(row.diagnostics_json), "updated_at": row.updated_at} for row in rows]

    def get_compilation(self):
        self._ensure()
        with self.engine.connect() as db: row = db.execute(select(compilation).order_by(compilation.c.updated_at.desc())).first()
        if not row: return None
        matches = [DrawWindowMatch.model_validate(x) for x in json.loads(row.matches_json)]
        status = "unavailable" if row.status == "empty" else row.status
        return DrawCompilation(compilation_id=f"comp-{row.id}", identity=getattr(row, "identity", ""), booking_code=row.booking_code or None, selection_count=len(matches), prediction_dates=[date.fromisoformat(x) for x in json.loads(row.prediction_dates_json)], matches=matches, status=status, diagnostics=json.loads(getattr(row, "diagnostics_json", "[]")), created_at=row.created_at, updated_at=row.updated_at)

    def promote_compilation(self, booking_code: str, dates: list[date], matches: list[DrawWindowMatch], identity: str):
        self._ensure(); now = datetime.now(timezone.utc); values = dict(booking_code=booking_code, identity=identity, matches_json=self._dump_matches(matches), prediction_dates_json=json.dumps([d.isoformat() for d in dates]), diagnostics_json="[]", status="active", updated_at=now)
        with self.engine.begin() as db:
            row = db.execute(select(compilation.c.id)).first()
            if row: db.execute(compilation.update().where(compilation.c.id == row.id).values(**values))
            else: db.execute(compilation.insert().values(**values, created_at=now))

    def unavailable_compilation(self, dates: list[date], *, status: str = "unavailable", diagnostics: list[str] | None = None, matches: list[DrawWindowMatch] | None = None, identity: str = ""):
        self._ensure(); now = datetime.now(timezone.utc); values = dict(booking_code="", identity=identity, matches_json=self._dump_matches(matches or []), prediction_dates_json=json.dumps([d.isoformat() for d in dates]), diagnostics_json=json.dumps(diagnostics or []), status=status, updated_at=now)
        with self.engine.begin() as db:
            row = db.execute(select(compilation.c.id)).first()
            if row: db.execute(compilation.update().where(compilation.c.id == row.id).values(**values))
            else: db.execute(compilation.insert().values(**values, created_at=now))


forebet_draw_store = ForebetDrawStore()
