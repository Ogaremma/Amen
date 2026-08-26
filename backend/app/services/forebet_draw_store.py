from __future__ import annotations

import json
from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Column, Date, DateTime, Integer, MetaData, String, Table, Text, UniqueConstraint, create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

from app.config.settings import get_settings
from app.schemas.forebet_draw_window import DrawCompilation, DrawWindowDay, DrawWindowMatch
from app.services.history_store import _database_url

metadata = MetaData()
daily = Table("forebet_draw_daily_bookings", metadata, Column("id", Integer, primary_key=True), Column("prediction_date", Date, nullable=False, unique=True), Column("booking_code", String(64), nullable=False), Column("identity", String(128), nullable=False, default=""), Column("status", String(16), nullable=False), Column("matches_json", Text, nullable=False), Column("source_urls_json", Text, nullable=False), Column("diagnostics_json", Text, nullable=False), Column("monitoring_json", Text, nullable=False, default="{}"), Column("created_at", DateTime(timezone=True), nullable=False), Column("updated_at", DateTime(timezone=True), nullable=False))
prebooking = Table("forebet_draw_prebooking_candidates", metadata, Column("id", Integer, primary_key=True), Column("prediction_date", Date, nullable=False, unique=True), Column("candidates_json", Text, nullable=False), Column("diagnostics_json", Text, nullable=False), Column("updated_at", DateTime(timezone=True), nullable=False))
compilation = Table("forebet_draw_compilation", metadata, Column("id", Integer, primary_key=True), Column("booking_code", String(64), nullable=False), Column("identity", String(128), nullable=False, default=""), Column("matches_json", Text, nullable=False), Column("prediction_dates_json", Text, nullable=False), Column("diagnostics_json", Text, nullable=False, default="[]"), Column("status", String(16), nullable=False), Column("created_at", DateTime(timezone=True), nullable=False), Column("updated_at", DateTime(timezone=True), nullable=False))
revisions = Table("forebet_draw_booking_revisions", metadata, Column("id", Integer, primary_key=True), Column("prediction_date", Date, nullable=False), Column("booking_code", String(64), nullable=False), Column("matches_json", Text, nullable=False), Column("is_current", Boolean, nullable=False), Column("created_at", DateTime(timezone=True), nullable=False), UniqueConstraint("prediction_date", "booking_code", name="uq_draw_revision_date_code"))
daily_batches = Table("forebet_draw_daily_batches", metadata, Column("id", Integer, primary_key=True), Column("prediction_date", Date, nullable=False), Column("batch_index", Integer, nullable=False), Column("booking_code", String(64), nullable=False), Column("identity", String(128), nullable=False), Column("matches_json", Text, nullable=False), Column("status", String(16), nullable=False), Column("created_at", DateTime(timezone=True), nullable=False), Column("updated_at", DateTime(timezone=True), nullable=False), UniqueConstraint("prediction_date", "batch_index", name="uq_draw_daily_batch"))
compilation_batches = Table("forebet_draw_compilation_batches", metadata, Column("id", Integer, primary_key=True), Column("batch_index", Integer, nullable=False), Column("booking_code", String(64), nullable=False), Column("identity", String(128), nullable=False), Column("matches_json", Text, nullable=False), Column("status", String(16), nullable=False), Column("created_at", DateTime(timezone=True), nullable=False), Column("updated_at", DateTime(timezone=True), nullable=False), UniqueConstraint("batch_index", name="uq_draw_compilation_batch"))
rebook_events = Table("forebet_draw_rebook_events", metadata, Column("id", Integer, primary_key=True), Column("prediction_date", Date, nullable=True), Column("scope", String(16), nullable=False), Column("batch_index", Integer, nullable=True), Column("removed_json", Text, nullable=False), Column("reason_json", Text, nullable=False), Column("old_code", String(64)), Column("new_code", String(64)), Column("old_identity", String(128)), Column("new_identity", String(128)), Column("created_at", DateTime(timezone=True), nullable=False))
raw_snapshots = Table("forebet_raw_snapshots", metadata, Column("id", Integer, primary_key=True), Column("source", String(64), nullable=False), Column("prediction_date", Date, nullable=False), Column("source_url", Text, nullable=False), Column("retrieved_at", DateTime(timezone=True), nullable=False), Column("raw_content", Text, nullable=False), Column("content_hash", String(64), nullable=False))
sportybet_snapshots = Table("sportybet_fixture_snapshots", metadata, Column("id", Integer, primary_key=True), Column("source", String(64), nullable=False), Column("retrieved_at", DateTime(timezone=True), nullable=False), Column("raw_content", Text, nullable=False), Column("content_hash", String(64), nullable=False))
acquisition_state = Table("forebet_acquisition_state", metadata,
    Column("prediction_date", Date, primary_key=True), Column("status", String(32), nullable=False),
    Column("last_attempt_at", DateTime(timezone=True), nullable=False), Column("last_success_at", DateTime(timezone=True)),
    Column("last_failure_at", DateTime(timezone=True)), Column("error_reason", Text), Column("details_json", Text, nullable=False, default="{}"))
job_locks = Table("forebet_job_locks", metadata, Column("lock_name", String(64), primary_key=True), Column("owner_id", String(128), nullable=False), Column("expires_at", DateTime(timezone=True), nullable=False), Column("updated_at", DateTime(timezone=True), nullable=False))


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
                for statement in (
                    "ALTER TABLE forebet_draw_daily_bookings ADD COLUMN identity VARCHAR(128) NOT NULL DEFAULT ''",
                    "ALTER TABLE forebet_draw_daily_bookings ADD COLUMN monitoring_json TEXT NOT NULL DEFAULT '{}'",
                ):
                    try: db.exec_driver_sql(statement)
                    except Exception: pass
                try:
                    db.exec_driver_sql("ALTER TABLE forebet_draw_compilation ADD COLUMN diagnostics_json TEXT NOT NULL DEFAULT '[]'")
                except Exception:
                    pass
            self._initialized = True

    @staticmethod
    def _dump_matches(matches): return json.dumps([m.model_dump(mode="json") for m in matches])

    @staticmethod
    def _batch_identity(matches: list[dict]) -> str:
        import hashlib
        identity = sorted((m["event_id"], m["market_id"], m["outcome_id"], m["product_id"], m["sport_id"], m.get("specifier") or "") for m in matches)
        return hashlib.sha256(repr(identity).encode()).hexdigest()

    def list_active(self, target_dates: list[date] | None = None) -> list[DrawWindowDay]:
        self._ensure()
        query = select(daily).where(daily.c.status.in_(("active", "unavailable", "error")))
        if target_dates is not None:
            query = query.where(daily.c.prediction_date.in_(target_dates))
        with self.engine.connect() as db: rows = db.execute(query.order_by(daily.c.prediction_date)).all()
        result = []
        for r in rows:
            diagnostics = json.loads(r.diagnostics_json)
            code = next((item.split(':', 1)[0] for item in diagnostics if ':' in item), None)
            batches = self.list_daily_batches(r.prediction_date)
            # Once batching exists it is the concurrency-protected source of
            # truth.  The flat daily row remains useful metadata and supports
            # databases created before batching, but must not resurrect a
            # selection removed by a successful batch CAS update.
            encoded_matches = [match for batch in batches for match in batch["matches"]] if batches else json.loads(r.matches_json)
            booking_code = batches[0]["booking_code"] if batches else (r.booking_code or None)
            identity = self._batch_identity(encoded_matches) if batches else getattr(r, "identity", "")
            result.append(DrawWindowDay(prediction_date=r.prediction_date, booking_code=booking_code, selection_count=len(encoded_matches), status=r.status, matches=[DrawWindowMatch.model_validate(x) for x in encoded_matches], source_urls=json.loads(r.source_urls_json), diagnostics=diagnostics, diagnostic_code=code, diagnostic_message=diagnostics[0] if diagnostics else None, identity=identity, created_at=r.created_at, last_updated=r.updated_at, acquisition=self.get_acquisition_state(r.prediction_date), batches=batches, monitoring=json.loads(getattr(r, "monitoring_json", "{}") or "{}"), rebook_events=self.list_rebook_events(r.prediction_date)))
        return result

    def get_daily_baseline_matches(self, prediction_date: date) -> list[DrawWindowMatch]:
        """Return the last acquired full-refresh set, before local batch pruning."""
        self._ensure()
        with self.engine.connect() as db:
            row = db.execute(select(daily.c.matches_json).where(daily.c.prediction_date == prediction_date)).first()
        return [DrawWindowMatch.model_validate(item) for item in json.loads(row.matches_json)] if row else []

    def promote(self, prediction_date: date, booking_code: str | None, matches: list[DrawWindowMatch], source_urls: list[str], diagnostics: list[str], status: str = "active"):
        self._ensure(); now = datetime.now(timezone.utc); encoded = self._dump_matches(matches); stored_code = booking_code or ""
        identity = __import__("hashlib").sha256(encoded.encode()).hexdigest()
        with self.engine.begin() as db:
            row = db.execute(select(daily).where(daily.c.prediction_date == prediction_date)).first()
            if row:
                db.execute(revisions.update().where(revisions.c.prediction_date == prediction_date).values(is_current=False))
                db.execute(daily.update().where(daily.c.prediction_date == prediction_date).values(booking_code=stored_code, identity=identity, status=status, matches_json=encoded, source_urls_json=json.dumps(source_urls), diagnostics_json=json.dumps(diagnostics), updated_at=now))
            else:
                db.execute(daily.insert().values(prediction_date=prediction_date, booking_code=stored_code, identity=identity, status=status, matches_json=encoded, source_urls_json=json.dumps(source_urls), diagnostics_json=json.dumps(diagnostics), monitoring_json="{}", created_at=now, updated_at=now))
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
        digest = hashlib.sha256(raw_content.encode()).hexdigest()
        with self.engine.begin() as db:
            exists = db.execute(select(raw_snapshots.c.id).where(raw_snapshots.c.source == source, raw_snapshots.c.prediction_date == prediction_date, raw_snapshots.c.content_hash == digest)).first()
            if exists:
                return False
            db.execute(raw_snapshots.insert().values(source=source, prediction_date=prediction_date, source_url=source_url, retrieved_at=datetime.now(timezone.utc), raw_content=raw_content, content_hash=digest))
            return True

    def save_sportybet_snapshot(self, raw_content: str, retrieved_at: datetime | None = None, *, source: str = "sportybet"):
        import hashlib
        self._ensure(); digest = hashlib.sha256(raw_content.encode()).hexdigest()
        with self.engine.begin() as db:
            exists = db.execute(select(sportybet_snapshots.c.id).where(sportybet_snapshots.c.source == source, sportybet_snapshots.c.content_hash == digest)).first()
            if exists: return False
            db.execute(sportybet_snapshots.insert().values(source=source, retrieved_at=retrieved_at or datetime.now(timezone.utc), raw_content=raw_content, content_hash=digest))
            return True

    def record_acquisition(self, prediction_date: date, status: str, details: dict, *, error_reason: str | None = None):
        self._ensure(); now = datetime.now(timezone.utc)
        with self.engine.begin() as db:
            row = db.execute(select(acquisition_state).where(acquisition_state.c.prediction_date == prediction_date)).first()
            values = {"status": status, "last_attempt_at": now, "details_json": json.dumps(details, default=str), "error_reason": error_reason}
            if status == "success": values.update(last_success_at=now, last_failure_at=None)
            elif error_reason: values["last_failure_at"] = now
            if row: db.execute(acquisition_state.update().where(acquisition_state.c.prediction_date == prediction_date).values(**values))
            else: db.execute(acquisition_state.insert().values(prediction_date=prediction_date, **values))

    def get_acquisition_state(self, prediction_date: date) -> dict:
        self._ensure()
        with self.engine.connect() as db: row = db.execute(select(acquisition_state).where(acquisition_state.c.prediction_date == prediction_date)).first()
        if not row: return {"status": "pending"}
        return {"status": row.status, "last_attempt_at": row.last_attempt_at, "last_success_at": row.last_success_at, "last_failure_at": row.last_failure_at, "error_reason": row.error_reason, **json.loads(row.details_json or "{}")}

    def acquire_job_lock(self, lock_name: str, owner_id: str, lease_seconds: int) -> bool:
        from datetime import timedelta
        self._ensure(); now = datetime.now(timezone.utc); expires = now + timedelta(seconds=lease_seconds)
        with self.engine.begin() as db:
            updated = db.execute(job_locks.update().where(job_locks.c.lock_name == lock_name, job_locks.c.expires_at <= now).values(owner_id=owner_id, expires_at=expires, updated_at=now))
            if updated.rowcount: return True
            try:
                db.execute(job_locks.insert().values(lock_name=lock_name, owner_id=owner_id, expires_at=expires, updated_at=now))
                return True
            except IntegrityError:
                return False

    def release_job_lock(self, lock_name: str, owner_id: str):
        self._ensure()
        with self.engine.begin() as db: db.execute(job_locks.delete().where(job_locks.c.lock_name == lock_name, job_locks.c.owner_id == owner_id))

    def list_prebooking(self) -> list[dict]:
        self._ensure()
        with self.engine.connect() as db:
            rows = db.execute(select(prebooking).order_by(prebooking.c.prediction_date)).all()
        return [{"prediction_date": row.prediction_date, "candidates": json.loads(row.candidates_json), "diagnostics": json.loads(row.diagnostics_json), "updated_at": row.updated_at} for row in rows]

    def get_compilation(self):
        self._ensure()
        with self.engine.connect() as db: row = db.execute(select(compilation).order_by(compilation.c.updated_at.desc())).first()
        if not row: return None
        batches = self.list_compilation_batches()
        encoded_matches = [match for batch in batches for match in batch["matches"]] if batches else json.loads(row.matches_json)
        matches = [DrawWindowMatch.model_validate(x) for x in encoded_matches]
        status = "unavailable" if row.status == "empty" else row.status
        booking_code = batches[0]["booking_code"] if batches else (row.booking_code or None)
        identity = self._batch_identity(encoded_matches) if batches else getattr(row, "identity", "")
        return DrawCompilation(compilation_id=f"comp-{row.id}", identity=identity, booking_code=booking_code, selection_count=len(matches), prediction_dates=[date.fromisoformat(x) for x in json.loads(row.prediction_dates_json)], matches=matches, status=status, diagnostics=json.loads(getattr(row, "diagnostics_json", "[]")), created_at=row.created_at, updated_at=row.updated_at, batches=batches, rebook_events=self.list_rebook_events())

    def promote_compilation(self, booking_code: str, dates: list[date], matches: list[DrawWindowMatch], identity: str):
        self._ensure(); now = datetime.now(timezone.utc); values = dict(booking_code=booking_code, identity=identity, matches_json=self._dump_matches(matches), prediction_dates_json=json.dumps([d.isoformat() for d in dates]), diagnostics_json="[]", status="active", updated_at=now)
        with self.engine.begin() as db:
            row = db.execute(select(compilation.c.id)).first()
            if row: db.execute(compilation.update().where(compilation.c.id == row.id).values(**values))
            else: db.execute(compilation.insert().values(**values, created_at=now))

    def list_daily_batches(self, prediction_date: date) -> list[dict]:
        self._ensure()
        with self.engine.connect() as db: rows = db.execute(select(daily_batches).where(daily_batches.c.prediction_date == prediction_date).order_by(daily_batches.c.batch_index)).all()
        return [{"batch_index": r.batch_index, "booking_code": r.booking_code or None, "identity": r.identity, "status": r.status, "matches": json.loads(r.matches_json), "created_at": r.created_at, "updated_at": r.updated_at} for r in rows]

    def replace_daily_batches(self, prediction_date: date, batches: list[dict], expected_identities: dict[int, str] | None = None) -> bool:
        self._ensure(); now = datetime.now(timezone.utc)
        with self.engine.begin() as db:
            if expected_identities:
                rows = db.execute(select(daily_batches.c.batch_index, daily_batches.c.identity).where(daily_batches.c.prediction_date == prediction_date)).all()
                current = {r.batch_index: r.identity for r in rows}
                if any(current.get(i) != identity for i, identity in expected_identities.items()): return False
            db.execute(daily_batches.delete().where(daily_batches.c.prediction_date == prediction_date))
            for item in batches:
                db.execute(daily_batches.insert().values(prediction_date=prediction_date, batch_index=item["batch_index"], booking_code=item.get("booking_code") or "", identity=item["identity"], matches_json=json.dumps(item.get("matches", []), default=str), status=item.get("status", "active"), created_at=now, updated_at=now))
        return True

    def replace_compilation_batches(self, batches: list[dict], expected_identities: dict[int, str] | None = None) -> bool:
        self._ensure(); now = datetime.now(timezone.utc)
        with self.engine.begin() as db:
            if expected_identities:
                rows = db.execute(select(compilation_batches.c.batch_index, compilation_batches.c.identity)).all()
                current = {r.batch_index: r.identity for r in rows}
                if any(current.get(i) != identity for i, identity in expected_identities.items()): return False
            db.execute(compilation_batches.delete())
            for item in batches:
                db.execute(compilation_batches.insert().values(batch_index=item["batch_index"], booking_code=item.get("booking_code") or "", identity=item["identity"], matches_json=json.dumps(item.get("matches", []), default=str), status=item.get("status", "active"), created_at=now, updated_at=now))
        return True

    def log_rebook_event(self, *, prediction_date, scope, batch_index, removed, reasons, old_code, new_code, old_identity, new_identity):
        self._ensure()
        with self.engine.begin() as db:
            db.execute(rebook_events.insert().values(prediction_date=prediction_date, scope=scope, batch_index=batch_index, removed_json=json.dumps(removed, default=str), reason_json=json.dumps(reasons, default=str), old_code=old_code, new_code=new_code, old_identity=old_identity, new_identity=new_identity, created_at=datetime.now(timezone.utc)))

    def update_day_monitoring(self, prediction_date: date, *, status: str, monitoring: dict) -> None:
        self._ensure()
        with self.engine.begin() as db:
            db.execute(daily.update().where(daily.c.prediction_date == prediction_date).values(status=status, monitoring_json=json.dumps(monitoring, default=str), updated_at=datetime.now(timezone.utc)))

    def update_daily_batch_if_identity(self, prediction_date: date, batch_index: int, expected_identity: str, item: dict) -> bool:
        self._ensure(); now = datetime.now(timezone.utc)
        with self.engine.begin() as db:
            result = db.execute(daily_batches.update().where(daily_batches.c.prediction_date == prediction_date, daily_batches.c.batch_index == batch_index, daily_batches.c.identity == expected_identity).values(booking_code=item.get("booking_code") or "", identity=item["identity"], matches_json=json.dumps(item.get("matches", []), default=str), status=item.get("status", "active"), updated_at=now))
            return result.rowcount == 1

    def list_compilation_batches(self) -> list[dict]:
        self._ensure()
        with self.engine.connect() as db: rows = db.execute(select(compilation_batches).order_by(compilation_batches.c.batch_index)).all()
        return [{"batch_index": r.batch_index, "booking_code": r.booking_code or None, "identity": r.identity, "status": r.status, "matches": json.loads(r.matches_json), "created_at": r.created_at, "updated_at": r.updated_at} for r in rows]

    def list_rebook_events(self, prediction_date: date | None = None) -> list[dict]:
        self._ensure(); query = select(rebook_events).order_by(rebook_events.c.created_at.desc())
        if prediction_date is not None: query = query.where(rebook_events.c.prediction_date == prediction_date)
        with self.engine.connect() as db: rows = db.execute(query).all()
        return [{"scope": r.scope, "batch_index": r.batch_index, "removed": json.loads(r.removed_json), "reasons": json.loads(r.reason_json), "old_code": r.old_code, "new_code": r.new_code, "timestamp": r.created_at} for r in rows]

    def unavailable_compilation(self, dates: list[date], *, status: str = "unavailable", diagnostics: list[str] | None = None, matches: list[DrawWindowMatch] | None = None, identity: str = ""):
        self._ensure(); now = datetime.now(timezone.utc); values = dict(booking_code="", identity=identity, matches_json=self._dump_matches(matches or []), prediction_dates_json=json.dumps([d.isoformat() for d in dates]), diagnostics_json=json.dumps(diagnostics or []), status=status, updated_at=now)
        with self.engine.begin() as db:
            row = db.execute(select(compilation.c.id)).first()
            if row: db.execute(compilation.update().where(compilation.c.id == row.id).values(**values))
            else: db.execute(compilation.insert().values(**values, created_at=now))


forebet_draw_store = ForebetDrawStore()
