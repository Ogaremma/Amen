import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase, mock

from app.schemas.forebet_draw_window import DrawWindowMatch
from app.services.forebet_draw_engine import ForebetDrawEngine
from app.services.forebet_draw_store import ForebetDrawStore
from app.services.forebet_draw_store import daily


class MissingTimeoutTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = TemporaryDirectory(); self.store = ForebetDrawStore(str(Path(self.tmp.name) / "state.sqlite3")); self.engine = ForebetDrawEngine(self.store); self.day = date.today()

    async def asyncTearDown(self): self.tmp.cleanup()

    async def seed(self, kickoff):
        match = DrawWindowMatch(event_id="missing", home_team="h", away_team="a", kickoff=kickoff, market_id="1", outcome_id="2", product_id=3, sport_id="sr:sport:1")
        self.store.promote(self.day, "PAPER-OLD", [match], [], [], status="active")
        self.store.replace_daily_batches(self.day, [{"batch_index": 1, "booking_code": "PAPER-OLD", "identity": self.engine._identity_hash([match]), "matches": [match.model_dump(mode="json")], "status": "active"}])

    async def test_missing_past_timeout_forces_terminal_and_exhausts(self):
        now = datetime.now(timezone.utc); await self.seed(now - timedelta(hours=7))
        with mock.patch("app.services.forebet_draw_engine.get_settings", return_value=mock.Mock(forebet_draw_missing_event_timeout_hours=6.0)):
            await self.engine.reconcile_statuses([], now=now)
        with self.store.engine.connect() as db: row = db.execute(daily.select().where(daily.c.prediction_date == self.day)).first()
        self.assertEqual(row.status, "complete")
        monitoring = json.loads(row.monitoring_json)
        self.assertTrue(monitoring["exhausted"])
        self.assertEqual(monitoring["statuses"]["missing"], "not_found_timeout_forced_terminal")
        self.assertEqual(self.store.list_rebook_events(self.day)[0]["reasons"], ["not_found_timeout_forced_terminal"])

    async def test_missing_inside_grace_period_keeps_day_open(self):
        now = datetime.now(timezone.utc); await self.seed(now - timedelta(hours=2))
        with mock.patch("app.services.forebet_draw_engine.get_settings", return_value=mock.Mock(forebet_draw_missing_event_timeout_hours=6.0)):
            await self.engine.reconcile_statuses([], now=now)
        with self.store.engine.connect() as db: row = db.execute(daily.select().where(daily.c.prediction_date == self.day)).first()
        self.assertEqual(row.status, "active")
        monitoring = json.loads(row.monitoring_json)
        self.assertFalse(monitoring["exhausted"])
        self.assertEqual(monitoring["statuses"]["missing"], "not_found_in_reconciliation")
