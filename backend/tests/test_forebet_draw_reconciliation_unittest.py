from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase
from app.schemas.forebet import SportyBetEvent
from app.schemas.forebet_draw_window import DrawWindowMatch
from app.services.forebet_draw_engine import ForebetDrawEngine
from app.services.forebet_draw_store import ForebetDrawStore

class ReconciliationTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self): self.tmp=TemporaryDirectory(); self.store=ForebetDrawStore(str(Path(self.tmp.name)/"s.db")); self.engine=ForebetDrawEngine(self.store); self.day=date.today()
    async def asyncTearDown(self): self.tmp.cleanup()
    def match(self, event_id, kickoff=None): return DrawWindowMatch(event_id=event_id,home_team="h",away_team="a",kickoff=kickoff or datetime.now(timezone.utc),market_id="1",outcome_id="2",product_id=3,sport_id="sr:sport:1")
    def event(self,event_id,status): return SportyBetEvent(event_id=event_id,home_team="h",away_team="a",home_team_name="h",away_team_name="a",competition="l",kickoff=datetime.now(timezone.utc),sport_id="sr:sport:1",market_id="1",outcome_draw_id="2",product_id=3,match_status=status)
    def seed(self, matches):
        self.store.promote(self.day,"PAPER-OLD",matches,[],[],status="active"); self.store.replace_daily_batches(self.day,[{"batch_index":1,"booking_code":"PAPER-OLD","identity":self.engine._identity_hash(matches),"matches":[m.model_dump(mode="json") for m in matches],"status":"active"}])
    async def test_finished_and_cancelled_exhaust(self):
        self.seed([self.match("a"),self.match("b")]); await self.engine.reconcile_statuses([self.event("a","Finished"),self.event("b","Cancelled")]); self.assertEqual(self.store.list_active(),[])
    async def test_live_keeps_open_and_real_reasons_logged(self):
        self.seed([self.match("a"),self.match("b")]); await self.engine.reconcile_statuses([self.event("a","Finished"),self.event("b","Live")]); day=self.store.list_active()[0]; self.assertEqual(day.status,"active"); reasons=self.store.list_rebook_events(self.day)[0]["reasons"]; self.assertIn("finished",reasons); self.assertIn("live",reasons); self.assertNotIn("presumed_live_by_kickoff",reasons)
    async def test_presumed_live_is_restored_when_upcoming(self):
        future=datetime.now(timezone.utc)+timedelta(hours=3); original=self.match("a",datetime.now(timezone.utc)-timedelta(minutes=1)); self.seed([original]); await self.engine.prune_kickoff_passed(); pruned=self.store.list_daily_batches(self.day)[0]; self.assertEqual(pruned["matches"],[]); await self.engine.reconcile_statuses([self.event("a","Not started").model_copy(update={"kickoff":future})]); restored=self.store.list_daily_batches(self.day)[0]; self.assertEqual(restored["matches"][0]["event_id"],"a"); self.assertNotEqual(restored["identity"],pruned["identity"]); self.assertNotEqual(restored["booking_code"],pruned["booking_code"])
    async def test_missing_keeps_day_open(self):
        self.seed([self.match("a")]); await self.engine.reconcile_statuses([]); day=self.store.list_active()[0]; self.assertFalse(day.monitoring["exhausted"]); self.assertEqual(day.monitoring["statuses"]["a"],"not_found_in_reconciliation")
