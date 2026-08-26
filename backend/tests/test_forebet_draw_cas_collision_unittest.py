from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from app.schemas.forebet_draw_window import DrawWindowMatch
from app.services.forebet_draw_engine import ForebetDrawEngine
from app.services.forebet_draw_store import ForebetDrawStore


class CasCollisionTests(TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.store = ForebetDrawStore(str(Path(self.tmp.name) / "state.sqlite3"))
        self.engine = ForebetDrawEngine(self.store)
        self.day = date(2030, 1, 1)
        now = datetime.now(timezone.utc)
        self.matches = [
            DrawWindowMatch(event_id="past", home_team="h1", away_team="a1", kickoff=now - timedelta(minutes=1), market_id="1", outcome_id="2", product_id=3, sport_id="sr:sport:1"),
            DrawWindowMatch(event_id="future", home_team="h2", away_team="a2", kickoff=now + timedelta(hours=1), market_id="1", outcome_id="2", product_id=3, sport_id="sr:sport:1"),
        ]
        identity = self.engine._identity_hash(self.matches)
        self.store.promote(self.day, "PAPER-INITIAL", self.matches, [], [], status="active")
        self.store.replace_daily_batches(self.day, [self.item(self.matches, identity, "PAPER-INITIAL")])

    def tearDown(self):
        self.tmp.cleanup()

    def item(self, matches, identity=None, code=None):
        identity = identity or self.engine._identity_hash(matches)
        return {"batch_index": 1, "booking_code": code or self.engine._paper_code(matches), "identity": identity, "matches": [m.model_dump(mode="json") for m in matches], "status": "active" if matches else "unavailable"}

    def write_and_log(self, snapshot, desired, reason):
        replacement = self.item(desired)
        written = self.store.update_daily_batch_if_identity(self.day, 1, snapshot["identity"], replacement)
        if written:
            old_ids = {m["event_id"] for m in snapshot["matches"]}
            new_ids = {m.event_id for m in desired}
            self.store.log_rebook_event(prediction_date=self.day, scope="daily", batch_index=1, removed=sorted(old_ids - new_ids), reasons=[reason], old_code=snapshot["booking_code"], new_code=replacement["booking_code"], old_identity=snapshot["identity"], new_identity=replacement["identity"])
        return written

    def test_prune_wins_stale_refresh_is_rejected_then_retry_succeeds(self):
        prune_read = self.store.list_daily_batches(self.day)[0]
        refresh_read = self.store.list_daily_batches(self.day)[0]

        self.assertTrue(self.write_and_log(prune_read, [self.matches[1]], "presumed_live_by_kickoff"))
        self.assertFalse(self.write_and_log(refresh_read, [self.matches[0]], "finished"))
        self.assertEqual(len(self.store.list_rebook_events(self.day)), 1)
        self.assertEqual([m["event_id"] for m in self.store.list_daily_batches(self.day)[0]["matches"]], ["future"])

        refresh_retry = self.store.list_daily_batches(self.day)[0]
        self.assertTrue(self.write_and_log(refresh_retry, [self.matches[0]], "finished"))
        self.assertEqual([m["event_id"] for m in self.store.list_daily_batches(self.day)[0]["matches"]], ["past"])
        self.assertEqual(len(self.store.list_rebook_events(self.day)), 2)

    def test_refresh_wins_stale_prune_is_rejected_then_retry_succeeds(self):
        refresh_read = self.store.list_daily_batches(self.day)[0]
        prune_read = self.store.list_daily_batches(self.day)[0]

        self.assertTrue(self.write_and_log(refresh_read, [self.matches[0]], "finished"))
        self.assertFalse(self.write_and_log(prune_read, [self.matches[1]], "presumed_live_by_kickoff"))
        self.assertEqual(len(self.store.list_rebook_events(self.day)), 1)
        self.assertEqual([m["event_id"] for m in self.store.list_daily_batches(self.day)[0]["matches"]], ["past"])

        prune_retry = self.store.list_daily_batches(self.day)[0]
        self.assertTrue(self.write_and_log(prune_retry, [], "presumed_live_by_kickoff"))
        self.assertEqual(self.store.list_daily_batches(self.day)[0]["matches"], [])
        self.assertEqual(len(self.store.list_rebook_events(self.day)), 2)
