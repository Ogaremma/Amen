import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from app.services.forebet_draw_engine import ForebetDrawEngine
from app.services.forebet_draw_store import ForebetDrawStore
from app.schemas.forebet_draw_window import DrawWindowMatch

class PruneTests(unittest.IsolatedAsyncioTestCase):
    async def test_prunes_past_only_logs_and_keeps_day_open(self):
        store = ForebetDrawStore(tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False).name); engine = ForebetDrawEngine(store)
        day = date.today(); past=(datetime.now(timezone.utc)-timedelta(minutes=2)).isoformat(); future=(datetime.now(timezone.utc)+timedelta(hours=2)).isoformat()
        old=[{"event_id":"past","home_team":"h","away_team":"a","kickoff":past,"market_id":"1","outcome_id":"2","product_id":3,"sport_id":"sr:sport:1"},{"event_id":"future","home_team":"h2","away_team":"a2","kickoff":future,"market_id":"1","outcome_id":"2","product_id":3,"sport_id":"sr:sport:1"}]
        store.replace_daily_batches(day,[{"batch_index":1,"booking_code":"PAPER-OLD","identity":"old","matches":old,"status":"active"}]); store.promote(day,"PAPER-OLD",[],[],[],status="active")
        await engine.prune_kickoff_passed(now=datetime.now(timezone.utc))
        batch=store.list_daily_batches(day)[0]; self.assertEqual([m["event_id"] for m in batch["matches"]],["future"]); self.assertNotEqual(batch["booking_code"],"PAPER-OLD"); self.assertEqual(store.list_active()[0].status,"active"); self.assertEqual(store.list_rebook_events(day)[0]["reasons"],["presumed_live_by_kickoff"])

    async def test_daily_prune_cascades_into_compilation_batches(self):
        store = ForebetDrawStore(tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False).name); engine = ForebetDrawEngine(store)
        day = date.today(); past=(datetime.now(timezone.utc)-timedelta(minutes=2)).isoformat(); future=(datetime.now(timezone.utc)+timedelta(hours=2)).isoformat()
        matches=[{"event_id":"past","home_team":"h","away_team":"a","kickoff":past,"market_id":"1","outcome_id":"2","product_id":3,"sport_id":"sr:sport:1"},{"event_id":"future","home_team":"h2","away_team":"a2","kickoff":future,"market_id":"1","outcome_id":"2","product_id":3,"sport_id":"sr:sport:1"}]
        models = [DrawWindowMatch.model_validate(m) for m in matches]
        identity = engine._identity_hash(models)
        item={"batch_index":1,"booking_code":"PAPER-OLD","identity":identity,"matches":matches,"status":"active"}
        store.promote(day,"PAPER-OLD",models,[],[],status="active")
        store.replace_daily_batches(day,[item]); store.promote_compilation("PAPER-COMP",[day],models,identity); store.replace_compilation_batches([item])
        await engine.prune_kickoff_passed(now=datetime.now(timezone.utc))
        self.assertEqual([m["event_id"] for m in store.list_compilation_batches()[0]["matches"]],["future"])
